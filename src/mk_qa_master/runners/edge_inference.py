"""v1.1.0 — Edge AI Inference Runner.

Theme G from `docs/prd-v1.1-edge-ai-runner.md`. The runner subclasses
TestRunner and lives alongside Maestro / pytest-playwright / etc.,
sharing the same report.json + history archive + reporter wiring.

Lifecycle (mirrors MaestroRunner's device-mgmt pattern):

  setup()   — start the RTSP source if QA_RTSP_SOURCE is set; healthcheck
              the remote inference target when not in desktop mode.
  run_tests — set EDGE_* env vars from QA_*, invoke pytest, archive
              the result. Catches setup failures cleanly so teardown
              still runs.
  teardown()— stop the RTSP source subprocess(es).

Phase 3 (v1.2) will replace the healthcheck stub with real HTTP probes
once RemoteHTTP.infer() is wired. For v1.1 desktop mode is the only
production path; healthcheck is a guard rail for users who set
QA_JETSON_HOST / QA_INFERENCE_ENDPOINT prematurely.
"""
from __future__ import annotations

import json
import os
from typing import Any

from .base import TestRunner
from ..config import (
    PROJECT_ROOT, REPORT_PATH, JUNIT_PATH, ARTIFACTS_DIR,
    EdgeConfig,
)
from ..security import safe_run
from ..edge.rtsp_source import SourceHandle, start_rtsp_source


_PYTEST_BASE_CMD = [
    "pytest",
    "--json-report",
    f"--json-report-file={REPORT_PATH}",
    f"--junitxml={JUNIT_PATH}",
]


class EdgeInferenceRunner(TestRunner):
    """RTSP source + inference backend → pytest → IoU / latency / FPS
    assertions. Aliased as `edge` and `rtsp` in the runner REGISTRY."""

    name = "edge"

    def __init__(self) -> None:
        super().__init__()
        self.cfg = EdgeConfig()
        self._source: SourceHandle | None = None

    # ---- lifecycle (Maestro-style device mgmt) --------------------------

    def setup(self) -> None:
        """Bring up the RTSP source + verify the inference target is
        reachable. Idempotent — safe to call once per test invocation.

        Skipped paths:
          - QA_RTSP_SOURCE empty → no source to start; pytest will fail
            cleanly at `cv2.VideoCapture("")` instead of here. We don't
            crash setup just because the user hasn't configured a
            source yet.
        """
        if self.cfg.rtsp_source:
            self._source = start_rtsp_source(self.cfg)
        if not self.cfg.desktop_mode:
            self._healthcheck_device()

    def teardown(self) -> None:
        """Stop the RTSP source if we started one. Always safe to call.
        SourceHandle.stop() swallows ProcessLookupError etc., so this
        never raises during cleanup."""
        if self._source is not None:
            self._source.stop()
            self._source = None

    def _healthcheck_device(self) -> None:
        """Probe the remote inference host before tests fire. v1.1 ships
        a stub that just checks the URL has the expected shape — the
        actual HTTP probe lands in v1.2 (Phase 3) alongside the real
        RemoteHTTP.infer() implementation.

        Raising here aborts setup → teardown still runs → tests don't
        run against a misconfigured remote target.
        """
        target = (
            self.cfg.inference_url
            or (f"http://{self.cfg.jetson_host}:8000/infer"
                if self.cfg.jetson_host else "")
        )
        if not target:
            return
        if not target.startswith(("http://", "https://")):
            raise RuntimeError(
                f"Edge runner: inference target {target!r} doesn't look "
                "like an HTTP URL. Check QA_INFERENCE_ENDPOINT / "
                "QA_JETSON_HOST."
            )
        # Phase 3 will replace this with a real GET /health probe.

    # ---- env plumbing ----------------------------------------------------

    def _edge_env(self) -> dict[str, str]:
        """EDGE_* env vars consumed by the generated `test_*.py`
        functions. Separated from QA_* so tests don't reach back into
        the config object — they just read os.environ via the fixture
        conftest.
        """
        return {
            "EDGE_RTSP_URL": (
                self._source.url if self._source
                else self.cfg.rtsp_source
            ),
            "EDGE_MIN_FPS": str(self.cfg.min_fps),
            "EDGE_LATENCY_SLA_MS": str(self.cfg.latency_sla_ms),
            "EDGE_IOU_THRESHOLD": str(self.cfg.iou_threshold),
        }

    # ---- TestRunner interface -------------------------------------------

    def list_tests(self) -> str:
        result = safe_run(
            ["pytest", "--collect-only", "-q"], cwd=PROJECT_ROOT,
        )
        return result.stdout or result.stderr

    def run_tests(self, filter: str | None = None, **kwargs: Any) -> dict:
        """Setup → pytest → teardown, guaranteed.

        We wrap the actual pytest invocation in try/finally so the
        RTSP source always gets torn down even if pytest itself
        crashes. Filter falls through to `pytest -k <filter>`.
        """
        cmd = list(_PYTEST_BASE_CMD)
        if filter:
            cmd.extend(["-k", filter])
        try:
            try:
                self.setup()
            except Exception as setup_exc:
                return {
                    "exit_code": 2,
                    "stdout_tail": "",
                    "stderr_tail": (
                        f"[edge] setup failed: "
                        f"{type(setup_exc).__name__}: {setup_exc}"
                    ),
                    "retry_enabled": False,
                }
            # Build env AFTER setup so EDGE_RTSP_URL picks up the
            # local-server URL the source manager just generated.
            env = {**os.environ, **self._edge_env()}
            result = safe_run(cmd, cwd=PROJECT_ROOT, env=env)
            return {
                "exit_code": result.returncode,
                "stdout_tail": result.stdout[-2000:],
                "stderr_tail": result.stderr[-1000:],
                "retry_enabled": False,
                "rtsp_url": env["EDGE_RTSP_URL"],
                "backend_mode": (
                    "desktop" if self.cfg.desktop_mode else "remote"
                ),
            }
        finally:
            self.teardown()

    def run_failed(self) -> dict:
        """Re-run only failing tests from the last report. Inherits the
        same setup/teardown discipline as run_tests."""
        return self.run_tests(filter=None, _extra_args=["--lf"])

    def get_report_summary(self) -> dict:
        if not REPORT_PATH.exists():
            return {"error": "找不到報告，請先執行 run_tests"}
        data = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        summary = data.get("summary", {}) or {}
        return {
            "total": summary.get("total", 0),
            "passed": summary.get("passed", 0),
            "failed": summary.get("failed", 0),
            "skipped": summary.get("skipped", 0),
            "duration": data.get("duration"),
            "backend_mode": (
                "desktop" if self.cfg.desktop_mode else "remote"
            ),
        }

    def get_failure_details(self, test_id: str | None = None) -> list[dict]:
        if not REPORT_PATH.exists():
            return [{"error": "找不到報告"}]
        data = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        failures = [
            t for t in data.get("tests", []) if t.get("outcome") == "failed"
        ]
        if test_id:
            failures = [t for t in failures if test_id in t.get("nodeid", "")]
        return [
            {
                "nodeid": t["nodeid"],
                "message": t.get("call", {}).get("longrepr", ""),
                "duration": t.get("call", {}).get("duration"),
            }
            for t in failures
        ]

    def generate_test(self, description: str, filename: str) -> str:
        """v1.1 PR-1 placeholder. The `generate_test` MCP tool's edge
        template lands in PR-3 alongside `analyze_stream`. For now,
        delegate to the description-only stub so the runner's abstract
        contract is satisfied — PR-3 replaces this with the real
        template that emits IoU + latency + throughput assertions.
        """
        target = PROJECT_ROOT / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            f'"""{description}\n\nEdge runner placeholder — PR-3 wires '
            'the full template."""\n\n'
            "def test_placeholder():\n"
            "    pass\n",
            encoding="utf-8",
        )
        return str(target.relative_to(PROJECT_ROOT))
