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
        """Probe the remote inference host before tests fire.

        v1.1 shipped a URL-shape-only stub; v1.2.0 (Phase 3) lands the
        real GET /health probe. v1.1's shape gate is kept as the first
        check — fail fast on obviously-broken config without hitting
        the network.

        The /health endpoint is derived from the inference endpoint:
        the trailing `/infer` segment is replaced with `/health`. Users
        running non-conforming endpoints (e.g., custom `/predict` path)
        get `<base>/health` probed; document the convention.

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

        # v1.2.0 — real GET /health probe. Derive /health from the
        # inference endpoint by replacing the trailing path segment.
        if target.endswith("/infer"):
            health_url = target[: -len("/infer")] + "/health"
        else:
            health_url = target.rstrip("/") + "/health"

        try:
            import requests  # type: ignore[import-not-found]
        except ImportError as e:  # pragma: no cover — install-time guidance
            raise RuntimeError(
                "Edge runner healthcheck requires the [edge] extras "
                "(requests). Install with: pip install "
                '"mk-qa-master[edge]". '
                f"Underlying ImportError: {e}"
            ) from e
        try:
            response = requests.get(
                health_url, timeout=self.cfg.device_timeout_s,
            )
            response.raise_for_status()
        except Exception as e:
            raise RuntimeError(
                f"Edge runner: inference target unreachable at "
                f"{health_url!r} ({type(e).__name__}: {e}). Raise "
                "QA_DEVICE_TIMEOUT_S or verify the board is online."
            )

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

    def generate_test(
        self,
        description: str,
        filename: str,
        *,
        annotations_path: str = "",
        label: str = "",
        max_frame: int = 60,
        resilience_mode: str = "",
    ) -> str:
        """v1.1 PR-3 — emit a working pytest with IoU + throughput
        assertions, not a TODO stub. Honors PRD §11 #4 ("no stubs").

        The generated file imports fixtures from
        `mk_qa_master.edge.pytest_plugin` (backend / stream / latency),
        reads `EDGE_*` env vars set by the runner, and asserts:

          - the given `label` is detected at some frame within the
            ground-truth window (IoU ≥ threshold)
          - p95 single-frame latency ≤ SLA
          - throughput stays at or above min_fps

        Defaults make the template work without an annotations file —
        the label-aware detection test is skipped and only throughput
        + latency assertions remain.

        v1.3.0 — `resilience_mode` opt-in adds degradation injection.
        When set to "netem", a session-scoped autouse fixture wraps
        the test in `apply_netem()` / `clear_netem()` from
        `mk_qa_master.edge.resilience`. The fixture is gated on
        `pytest.importorskip` + the helper's own
        `QA_EDGE_NETEM_ENABLED` consent check + Linux platform —
        cleanly skips on macOS/Windows or when the consent gate is
        unset. Default ("") emits no resilience fixture (backward
        compat for non-resilience tests).
        """
        target = PROJECT_ROOT / filename
        target.parent.mkdir(parents=True, exist_ok=True)

        # Python identifier for the per-label test function name. Defaults
        # to 'target' when no label is provided so the file is still
        # importable.
        ident = "".join(
            ch if ch.isalnum() or ch == "_" else "_"
            for ch in (label or "target").lower()
        )

        resilience_block = ""
        if resilience_mode == "netem":
            resilience_block = _NETEM_RESILIENCE_FIXTURE

        target.write_text(
            EDGE_TEST_TEMPLATE.format(
                description=description,
                annotations_path=annotations_path,
                label=label,
                ident=ident,
                max_frame=int(max_frame),
                resilience_block=resilience_block,
            ),
            encoding="utf-8",
        )
        return str(target.relative_to(PROJECT_ROOT))


# v1.3.0 — netem resilience fixture, injected into the generated file
# when `resilience_mode="netem"` is passed to `generate_test`. Triple-quoted
# raw string so the apostrophes in the docstring don't fight the outer
# triple-quoted EDGE_TEST_TEMPLATE.
_NETEM_RESILIENCE_FIXTURE = '''

# v1.3.0 — netem resilience injection (auto-skips when:
#  - pytest.importorskip can't find the resilience module
#  - QA_EDGE_NETEM_ENABLED is unset / not "true" (raised by apply_netem)
#  - sys.platform != "linux" (raised by both apply_netem and clear_netem))
@pytest.fixture(scope="session", autouse=True)
def _resilience():
    pytest.importorskip("mk_qa_master.edge.resilience")
    from mk_qa_master.edge.resilience import apply_netem, clear_netem
    try:
        apply_netem(jitter_ms=80, loss_pct=2)
    except RuntimeError as exc:
        pytest.skip(f"resilience-mode netem unavailable: {exc}")
    try:
        yield
    finally:
        try:
            clear_netem()
        except RuntimeError:
            pass  # already skipped above; teardown is best-effort

'''


# Edge runner test template (spec §10). Produces a self-contained
# pytest file: one detection test (skipped when no annotations) + one
# throughput test. Reads EDGE_* env vars the runner populates.
EDGE_TEST_TEMPLATE = '''"""{description}

Auto-generated by mk-qa-master EdgeInferenceRunner.
Fixtures (backend / stream / latency) come from mk_qa_master.edge.pytest_plugin.
"""
import json
import os
import time

import pytest

from mk_qa_master.edge.metrics import match_detection
from mk_qa_master.edge.pytest_plugin import backend, latency, stream  # noqa: F401

ANN_PATH = {annotations_path!r}
LABEL = {label!r}
MAX_FRAME = {max_frame}

IOU = float(os.environ.get("EDGE_IOU_THRESHOLD", "0.5"))
MIN_FPS = float(os.environ.get("EDGE_MIN_FPS", "25"))
SLA = float(os.environ.get("EDGE_LATENCY_SLA_MS", "40"))

{resilience_block}
def _load_annotations():
    if not ANN_PATH:
        return {{}}
    try:
        with open(ANN_PATH) as f:
            return json.load(f).get("frames", {{}}) or {{}}
    except (OSError, json.JSONDecodeError):
        return {{}}


@pytest.mark.skipif(not LABEL, reason="no annotations / label provided")
def test_detect_{ident}(stream, backend, latency):
    """Iterate up to MAX_FRAME frames; assert the target label appears
    in at least one frame within the IoU threshold + p95 latency holds."""
    ann = _load_annotations()
    hit = False
    frame_idx = 0
    while frame_idx <= MAX_FRAME:
        ok, frame = stream.read()
        if not ok:
            break
        res = backend.infer(frame)
        latency.add(res.latency_ms)
        for exp in ann.get(str(frame_idx), []):
            if exp.get("label") == LABEL and match_detection(res.detections, exp, IOU):
                hit = True
        frame_idx += 1
    assert hit, f"label {{LABEL!r}} not detected within IoU={{IOU}} in {{MAX_FRAME}} frames"
    assert latency.p95() <= SLA, f"p95 latency exceeded SLA: {{latency.p95():.1f}} ms > {{SLA}} ms"


def test_throughput(stream, backend, latency):
    """Sustained-rate check: at least MIN_FPS over a 150-frame window."""
    n = 0
    t0 = time.time()
    while n < 150:
        ok, frame = stream.read()
        if not ok:
            break
        latency.add(backend.infer(frame).latency_ms)
        n += 1
    elapsed = max(time.time() - t0, 1e-6)
    fps = n / elapsed
    assert fps >= MIN_FPS, f"throughput below target: {{fps:.1f}} fps < {{MIN_FPS}} fps"
'''
