"""Schemathesis runner — OpenAPI-driven property-based API tests.

Hand it an OpenAPI 3.x / Swagger 2.0 URL or file:// path and Schemathesis
auto-generates fuzz cases per endpoint (response_schema_conformance,
status_code_conformance, not_a_server_error, content_type_conformance,
response_headers_conformance). We wrap the CLI, parse its JSON report,
and translate to the same `report.json` shape pytest-json-report
produces so every downstream consumer (optimizer, history, HTML
reporter, get_failure_details) keeps working without runner-specific
branches.

Schemathesis is an optional dep: `pip install 'mk-qa-master[api]'`.
The runner imports it lazily so the base install stays slim and users
who never run API tests don't pay for the dependency.
"""
import json
import os
import re
import shutil
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from .base import TestRunner
from ..config import (
    PROJECT_ROOT,
    REPORT_PATH,
    JUNIT_PATH,
    HISTORY_DIR,
)
from ..security import safe_run


# Secret-redaction patterns. Applied to request bodies, response bodies, and
# the raw violation message before anything is written to disk. Disabled by
# QA_NO_REDACT=1 for debugging. Conservative on purpose: prefer false
# positives (over-redaction) to false negatives (leaking credentials into
# the history archive a user later shares).
_REDACT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Authorization: Bearer <anything>
    (re.compile(r"(Authorization:\s*Bearer\s+)\S+", re.IGNORECASE),
     r"\1[REDACTED]"),
    # "password": "<value>" — JSON-style key/value
    (re.compile(r'("password"\s*:\s*)"[^"]*"', re.IGNORECASE),
     r'\1"[REDACTED]"'),
    # "token": "<value>" or similar (api_key, access_token, secret, …)
    (re.compile(r'("(?:[a-z_]*token|api[-_]?key|secret|access_token|refresh_token)"\s*:\s*)"[^"]*"',
                re.IGNORECASE),
     r'\1"[REDACTED]"'),
]


def _redact(text: str | None) -> str | None:
    if text is None:
        return None
    if os.getenv("QA_NO_REDACT", "").lower() in ("1", "true", "yes"):
        return text
    out = text
    for pat, repl in _REDACT_PATTERNS:
        out = pat.sub(repl, out)
    return out


def _to_cli_target(url: str) -> str:
    """Schemathesis CLI accepts http(s):// URLs and plain filesystem paths
    but NOT file:// URLs. We accept file:// from users (PRD §21 decision 2,
    keeps the env-var shape unambiguous) and translate to a plain path here
    right before subprocess invocation."""
    if url.startswith("file://"):
        parsed = urlparse(url)
        return parsed.path
    return url


def _validate_openapi_url(url: str) -> None:
    """Accept http(s):// or file:// only. Plain paths require file://
    prefix to avoid relative-vs-absolute ambiguity (PRD §21 decision 2)."""
    if not url:
        raise ValueError(
            "QA_OPENAPI_URL is required for the schemathesis runner. "
            "Set it to an OpenAPI 3.x URL (http(s)://...) or file path "
            "(file://...)"
        )
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https", "file"):
        raise ValueError(
            "QA_OPENAPI_URL must be a http(s):// URL or file:// path "
            f"(got {url!r})"
        )


def _require_schemathesis_cli() -> str:
    """Lazy ImportError surface: confirm the CLI is on PATH and the Python
    module imports cleanly. Either failure ⇒ same install hint."""
    try:
        import schemathesis  # noqa: F401
    except ImportError as e:
        raise ImportError(
            "schemathesis is not installed. Install with: "
            "pip install 'mk-qa-master[api]'"
        ) from e
    cli = shutil.which("schemathesis")
    if not cli:
        raise ImportError(
            "schemathesis CLI not found on PATH. Install with: "
            "pip install 'mk-qa-master[api]'"
        )
    return cli


class SchemathesisRunner(TestRunner):
    name = "schemathesis"

    # ---- public TestRunner API --------------------------------------------

    def list_tests(self) -> str:
        url = self._openapi_url()
        cli = _require_schemathesis_cli()
        # `--dry-run` resolves the schema and emits the planned operation list
        # without issuing real HTTP. Verbosity stays low so the parse below
        # is straightforward.
        cmd = [cli, "run", "--dry-run", "--no-color", _to_cli_target(url)]
        result = safe_run(cmd, cwd=PROJECT_ROOT)
        text = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
        return self._format_operations(text)

    def run_tests(self, filter=None, **kwargs) -> dict:
        url = self._openapi_url()
        cli = _require_schemathesis_cli()
        report_json = PROJECT_ROOT / ".schemathesis-report.json"
        # Reset between runs so a stale file from a previous invocation can't
        # be mistaken for fresh output if the CLI errors before writing.
        for p in (report_json, JUNIT_PATH):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass

        cmd = self._base_cmd(cli, url, report_json)
        if filter:
            cmd.extend(["--include-path", filter])
        result = safe_run(cmd, cwd=PROJECT_ROOT)
        self._normalize_report(report_json)
        self._archive_report()
        post = self.get_report_summary()
        post_failed = (post.get("failed") or 0) if isinstance(post, dict) else 0
        return {
            "exit_code": 0 if post_failed == 0 else 1,
            "raw_exit_code": result.returncode,
            "openapi_url": url,
            "stdout_tail": result.stdout[-2000:] if result.stdout else "",
            "stderr_tail": result.stderr[-1000:] if result.stderr else "",
        }

    def run_failed(self) -> dict:
        if not REPORT_PATH.exists():
            return {"error": "no previous report.json; run tests first"}
        try:
            data = json.loads(REPORT_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            return {"error": "report.json invalid or unreadable"}
        # Failed nodeids look like "POST /pet :: response_conformance". We
        # collect distinct method+path pairs and pass them as
        # --include-method / --include-path filters. Schemathesis ANDs the
        # filters, so re-runs scope to exactly the failing operations.
        ops: set[tuple[str, str]] = set()
        for t in data.get("tests", []) or []:
            if t.get("outcome") != "failed":
                continue
            nodeid = t.get("nodeid") or ""
            head = nodeid.split("::")[0].strip()
            parts = head.split(" ", 1)
            if len(parts) == 2:
                ops.add((parts[0].strip().upper(), parts[1].strip()))
        if not ops:
            return {"info": "no previous failures to re-run"}

        url = self._openapi_url()
        cli = _require_schemathesis_cli()
        report_json = PROJECT_ROOT / ".schemathesis-report.json"
        try:
            report_json.unlink(missing_ok=True)
        except OSError:
            pass
        cmd = self._base_cmd(cli, url, report_json)
        for method, path in sorted(ops):
            cmd.extend(["--include-method", method, "--include-path", path])
        result = safe_run(cmd, cwd=PROJECT_ROOT)
        self._normalize_report(report_json)
        self._archive_report()
        post = self.get_report_summary()
        post_failed = (post.get("failed") or 0) if isinstance(post, dict) else 0
        return {
            "exit_code": 0 if post_failed == 0 else 1,
            "raw_exit_code": result.returncode,
            "ops_rerun": len(ops),
            "stdout_tail": result.stdout[-2000:] if result.stdout else "",
        }

    def get_report_summary(self) -> dict:
        if not REPORT_PATH.exists():
            return {"error": "找不到報告，請先執行 run_tests"}
        try:
            data = json.loads(REPORT_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            return {"error": "report.json invalid"}
        s = data.get("summary", {}) or {}
        return {
            "total": s.get("total", 0),
            "passed": s.get("passed", 0),
            "failed": s.get("failed", 0),
            "skipped": s.get("skipped", 0),
            "flaky_in_run": s.get("flaky_in_run", 0) or 0,
            "duration": data.get("duration"),
        }

    def get_failure_details(self, test_id=None) -> list[dict]:
        if not REPORT_PATH.exists():
            return [{"error": "找不到報告"}]
        try:
            data = json.loads(REPORT_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            return [{"error": "report.json invalid"}]
        failures = [t for t in data.get("tests", []) or [] if t.get("outcome") == "failed"]
        if test_id:
            failures = [t for t in failures if test_id in t.get("nodeid", "")]
        return [
            {
                "nodeid": t["nodeid"],
                "message": (t.get("call") or {}).get("longrepr", ""),
                "duration": (t.get("call") or {}).get("duration"),
                "artifacts": t.get("artifacts") or {},
                "screenshot": None,
                "trace": None,
                "video": None,
            }
            for t in failures
        ]

    def get_all_test_details(self) -> list[dict]:
        if not REPORT_PATH.exists():
            return []
        try:
            data = json.loads(REPORT_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            return []
        out: list[dict] = []
        for t in data.get("tests", []) or []:
            nodeid = t.get("nodeid")
            outcome = t.get("outcome")
            if not nodeid:
                continue
            out.append({
                "nodeid": nodeid,
                "title": nodeid,
                "outcome": outcome,
                "duration": (t.get("call") or {}).get("duration"),
                "message": (t.get("call") or {}).get("longrepr", "") if outcome == "failed" else "",
                "steps": [],
                "screenshot": None,
                "trace": None,
                "video": None,
                "artifacts": t.get("artifacts") or {},
            })
        return out

    def generate_test(
        self,
        description: str,
        filename: str,
        url: str | None = None,
        module: dict | None = None,
        business_context: str | None = None,
    ) -> str:
        # v0.6.0: Schemathesis generates cases internally from the schema —
        # users don't author files. v0.7.0 may add `--render-pytest` to emit
        # a standalone pytest harness; for now, surface the rationale.
        return (
            "schemathesis runner does not author test files — the OpenAPI "
            "schema is the source of truth, and `run_tests` fuzzes each "
            "operation against `--checks all`. v0.7.0 may add codegen to "
            "emit a runnable pytest+httpx harness."
        )

    def get_history(self, limit: int = 10) -> list[dict]:
        if not HISTORY_DIR.exists():
            return []
        files = sorted(HISTORY_DIR.glob("*.json"), reverse=True)[:limit]
        history: list[dict] = []
        for f in reversed(files):
            try:
                data = json.loads(f.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            s = data.get("summary", {}) or {}
            total = s.get("total", 0) or 0
            passed = s.get("passed", 0) or 0
            history.append({
                "file": f.name,
                "timestamp": f.stem,
                "total": total,
                "passed": passed,
                "failed": s.get("failed", 0) or 0,
                "skipped": s.get("skipped", 0) or 0,
                "duration": data.get("duration"),
                "pass_rate": (passed / total * 100) if total else 0,
            })
        return history

    # ---- internals --------------------------------------------------------

    def _openapi_url(self) -> str:
        # Read at call-time (not import-time) so tests / users who flip
        # env vars between method calls see the new value. Mirrors how
        # config.RUNNER_NAME is consulted lazily by get_runner().
        url = os.getenv("QA_OPENAPI_URL", "").strip()
        _validate_openapi_url(url)
        return url

    def _base_cmd(self, cli: str, url: str, report_json: Path) -> list[str]:
        checks_env = os.getenv("QA_SCHEMATHESIS_CHECKS", "").strip()
        max_examples = os.getenv("QA_SCHEMATHESIS_MAX_EXAMPLES", "20").strip() or "20"
        auth = os.getenv("QA_SCHEMATHESIS_AUTH", "").strip()
        dry_run = os.getenv("QA_SCHEMATHESIS_DRY_RUN", "").lower() in ("1", "true", "yes")

        # Schemathesis 3.x does NOT have a JSON-report flag; --junit-xml is
        # the only structured output. We parse JUnit XML in _normalize_report
        # and translate to mk-qa-master's report.json shape from there.
        cmd: list[str] = [cli, "run", "--hypothesis-database=none",
                          f"--hypothesis-max-examples={max_examples}",
                          f"--junit-xml={JUNIT_PATH}"]
        if checks_env:
            for chk in [c.strip() for c in checks_env.split(",") if c.strip()]:
                cmd.extend(["--checks", chk])
        else:
            cmd.extend(["--checks", "all"])
        if auth:
            cmd.extend(["-H", f"Authorization: {auth}"])
        if dry_run:
            cmd.append("--dry-run")
        cmd.append(_to_cli_target(url))
        return cmd

    def _format_operations(self, raw: str) -> str:
        """Best-effort filter: Schemathesis emits a noisy dry-run banner
        plus per-operation lines. We keep lines that look like HTTP method
        + path so list_tests reads cleanly; cap at 200 to mirror other
        runners' output ceiling."""
        op_re = re.compile(r"^\s*(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+\S+")
        keep: list[str] = []
        for line in raw.splitlines():
            stripped = line.strip()
            if op_re.match(stripped):
                keep.append(stripped)
        if not keep:
            # No operation lines parsed — surface a small tail of raw output
            # so the user can debug rather than seeing an empty string.
            tail = "\n".join(raw.splitlines()[-30:])
            return f"(no operations parsed; schemathesis output tail:)\n{tail}"
        if len(keep) > 200:
            keep = keep[:200] + [f"... ({len(keep) - 200} more, truncated)"]
        return "\n".join(keep)

    def _normalize_report(self, _legacy_arg: Path | None = None) -> None:
        """Translate Schemathesis JUnit XML into pytest-json-report shape.

        Schemathesis 3.x exposes only `--junit-xml` for structured output
        (no JSON report flag exists — the PRD §10 mapping that referenced
        `--report-json` was based on a stale assumption). JUnit XML is
        standard and stdlib-parseable, so we use it as the source of truth.

        Per-testcase mapping:
          <testcase name="METHOD /path"/>          → nodeid (no per-check
                                                     granularity in JUnit XML)
          <failure message=...>...</failure>       → outcome=failed, longrepr
          time="0.42"                              → duration
          <testsuite tests= failures= errors=>    → summary counts

        Request / response artifacts are NOT in JUnit XML; we leave the
        artifacts block stubbed. v0.7 may add --cassette-path parsing to
        recover those, but it doubles parser surface area.
        """
        # Default: empty synthetic report so downstream paths (history,
        # optimizer, HTML reporter) don't 404 even when schemathesis
        # crashed before writing anything.
        empty = {
            "summary": {"total": 0, "passed": 0, "failed": 0, "skipped": 0},
            "duration": 0,
            "tests": [],
        }
        if not JUNIT_PATH.exists():
            self._write_report(empty)
            return

        try:
            tree = ET.parse(JUNIT_PATH)
            root = tree.getroot()
        except (OSError, ET.ParseError):
            self._write_report(empty)
            return

        # JUnit XML may have <testsuites> wrapping <testsuite>s, or just a
        # single <testsuite>. Walk either shape.
        suites = root.findall("testsuite") if root.tag == "testsuites" else [root]

        tests: list[dict] = []
        passed = failed = skipped = errors = 0
        duration_total = 0.0

        for suite in suites:
            try:
                duration_total += float(suite.attrib.get("time", "0") or 0)
            except (TypeError, ValueError):
                pass
            for tc in suite.findall("testcase"):
                name = tc.attrib.get("name") or ""
                try:
                    dur = float(tc.attrib.get("time", "0") or 0)
                except (TypeError, ValueError):
                    dur = 0.0

                failure_el = tc.find("failure")
                error_el = tc.find("error")
                skipped_el = tc.find("skipped")

                if failure_el is not None or error_el is not None:
                    outcome = "failed"
                    failed += 1
                    msg_el = failure_el if failure_el is not None else error_el
                    message = (msg_el.attrib.get("message") or "").strip() or (msg_el.text or "").strip()
                elif skipped_el is not None:
                    outcome = "skipped"
                    skipped += 1
                    message = (skipped_el.attrib.get("message") or "").strip()
                else:
                    outcome = "passed"
                    passed += 1
                    message = ""

                tests.append({
                    "nodeid": name,
                    "outcome": outcome,
                    "message": _redact(message) or "",
                    "duration": dur,
                    "call": {"duration": dur, "longrepr": _redact(message) or ""},
                    "artifacts": {
                        "request_response": {
                            "method": name.split(" ", 1)[0] if " " in name else None,
                            "url": None,
                            "request_body": None,
                            "response_status": None,
                            "response_body": None,
                            "violation": None,
                        }
                    },
                })

        # Errors collapse into "failed" for the summary; JUnit distinguishes
        # them but mk-qa-master's existing pipeline only knows passed/failed/skipped.
        _ = errors
        report = {
            "summary": {
                "total": len(tests),
                "passed": passed,
                "failed": failed,
                "skipped": skipped,
            },
            "duration": duration_total,
            "tests": tests,
        }
        self._write_report(report)

    def _write_report(self, report: dict) -> None:
        try:
            REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
            REPORT_PATH.write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _archive_report(self) -> None:
        """Snapshot report.json + refresh optimizer plan. Mirrors the other
        runners so history / coach cadence stays identical."""
        if not REPORT_PATH.exists():
            return
        try:
            HISTORY_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            shutil.copy2(REPORT_PATH, HISTORY_DIR / f"{stamp}.json")
        except OSError:
            pass
        try:
            from ..tools import optimizer
            optimizer.write_plan()
        except Exception:
            pass
