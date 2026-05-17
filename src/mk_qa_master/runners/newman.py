"""Newman runner — Postman collection execution.

Hand it a Postman 2.x collection (plus optional environment / globals
files) and Newman 6.x replays each request, runs the embedded
`pm.test(...)` assertions, and emits a JSON report we can map directly
to mk-qa-master's `report.json` shape. The same downstream pipeline
(history, optimizer, HTML reporter, get_failure_details) keeps working
with no runner-specific branches.

Newman is an **npm** package, not pip, so there's no Python import to
gate. Detection is `shutil.which("newman")`; missing CLI → clear
ImportError with the install hint (`npm install -g newman`).

Phase 2 of the v0.6 API-testing arc (PRD §14 + §22). Sibling to the
v0.6.0 Schemathesis runner: mirrors the redaction policy, the report
normalization pattern, the history-archive hook, and the lazy
CLI-lookup so the two paths feel uniform to anyone reading the code.
"""
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from .base import TestRunner
from ..config import (
    PROJECT_ROOT,
    REPORT_PATH,
    HISTORY_DIR,
)
from ..security import safe_run


# Secret-redaction patterns. Applied to request bodies, response bodies, and
# the raw assertion message before anything is written to disk. Disabled by
# QA_NO_REDACT=1 for debugging. Conservative on purpose: prefer false
# positives (over-redaction) to false negatives (leaking credentials into
# the history archive a user later shares). Mirrors schemathesis.py so the
# two runners scrub the same shapes.
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


def _validate_collection_path(path: str) -> None:
    """Newman accepts plain filesystem paths only — no file:// scheme
    needed because collections are always local artifacts (PRD §22
    decision 5). Validate the path exists and is readable; defer the
    schema-version check to Newman itself."""
    if not path:
        raise ValueError(
            "QA_POSTMAN_COLLECTION is required for the newman runner. "
            "Set it to a plain filesystem path to a Postman 2.x collection.json."
        )
    p = Path(path)
    if not p.exists():
        raise ValueError(
            f"QA_POSTMAN_COLLECTION points to a missing file: {path!r}"
        )
    if not p.is_file():
        raise ValueError(
            f"QA_POSTMAN_COLLECTION must be a file, got: {path!r}"
        )


def _require_newman_cli() -> str:
    """Lazy ImportError surface: Newman is an npm package, so there's no
    Python `import newman` to attempt — `shutil.which` is the canonical
    detection. Same install-hint shape as the schemathesis runner so the
    two failure paths feel uniform to a reader."""
    cli = shutil.which("newman")
    if not cli:
        raise ImportError(
            "newman CLI not found on PATH. Install with: "
            "npm install -g newman  (Newman is an npm package, not pip)"
        )
    return cli


class NewmanRunner(TestRunner):
    name = "newman"

    # ---- public TestRunner API --------------------------------------------

    def list_tests(self) -> str:
        """Parse the collection JSON locally — no subprocess needed.

        Postman 2.1 collections nest items under `item[]`. Folders are
        items with their own `item[]`; leaf requests have a `request`
        block. We walk recursively and emit one line per leaf, formatted
        as `METHOD path :: <request name>` for parity with how
        list_tests reads on the other runners.
        """
        collection_path = self._collection_path()
        try:
            data = json.loads(Path(collection_path).read_text())
        except (OSError, json.JSONDecodeError) as e:
            return f"(could not parse collection JSON: {type(e).__name__}: {e})"

        lines: list[str] = []
        self._walk_items(data.get("item") or [], lines, parent_path=[])
        if not lines:
            return "(no requests found in collection)"
        if len(lines) > 200:
            lines = lines[:200] + [f"... ({len(lines) - 200} more, truncated)"]
        return "\n".join(lines)

    def run_tests(self, filter=None, **kwargs) -> dict:
        collection_path = self._collection_path()
        cli = _require_newman_cli()
        report_json = PROJECT_ROOT / ".newman-report.json"
        # Reset between runs so a stale file from a previous invocation can't
        # be mistaken for fresh output if the CLI errors before writing.
        try:
            report_json.unlink(missing_ok=True)
        except OSError:
            pass

        cmd = self._base_cmd(cli, collection_path, report_json)
        # `filter` is a single Postman folder name when passed through the
        # generic MCP `run_tests(filter=...)` surface. Newman supports
        # repeated --folder flags; we expose the multi-folder case via the
        # QA_POSTMAN_FOLDER env var (CSV).
        if filter:
            cmd.extend(["--folder", filter])
        result = safe_run(cmd, cwd=PROJECT_ROOT)
        self._normalize_report(report_json)
        self._archive_report()
        post = self.get_report_summary()
        post_failed = (post.get("failed") or 0) if isinstance(post, dict) else 0
        return {
            "exit_code": 0 if post_failed == 0 else 1,
            "raw_exit_code": result.returncode,
            "collection": collection_path,
            "stdout_tail": result.stdout[-2000:] if result.stdout else "",
            "stderr_tail": result.stderr[-1000:] if result.stderr else "",
        }

    def run_failed(self) -> dict:
        """Re-run only previously-failed requests, scoped via --folder.

        Newman's CLI doesn't expose per-request include filters the way
        Schemathesis does for operations, so we fall back to scoping by
        the parent folder of each failed item. If the collection has no
        folder structure (everything at the root), `run_failed` degrades
        to a full re-run — which is still useful because Newman 6.x is
        fast on small collections.
        """
        if not REPORT_PATH.exists():
            return {"error": "no previous report.json; run tests first"}
        try:
            data = json.loads(REPORT_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            return {"error": "report.json invalid or unreadable"}

        # Collect parent folder names from failed nodeids' artifacts.
        folders: set[str] = set()
        any_failed = False
        for t in data.get("tests", []) or []:
            if t.get("outcome") != "failed":
                continue
            any_failed = True
            parent = ((t.get("artifacts") or {}).get("request_response") or {}).get("parent_folder")
            if parent:
                folders.add(parent)
        if not any_failed:
            return {"info": "no previous failures to re-run"}

        collection_path = self._collection_path()
        cli = _require_newman_cli()
        report_json = PROJECT_ROOT / ".newman-report.json"
        try:
            report_json.unlink(missing_ok=True)
        except OSError:
            pass
        cmd = self._base_cmd(cli, collection_path, report_json)
        if folders:
            for f in sorted(folders):
                cmd.extend(["--folder", f])
            folders_used = len(folders)
        else:
            # No folder structure — full re-run is the only safe option.
            folders_used = 0
        result = safe_run(cmd, cwd=PROJECT_ROOT)
        self._normalize_report(report_json)
        self._archive_report()
        post = self.get_report_summary()
        post_failed = (post.get("failed") or 0) if isinstance(post, dict) else 0
        return {
            "exit_code": 0 if post_failed == 0 else 1,
            "raw_exit_code": result.returncode,
            "folders_rerun": folders_used,
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
        # v0.6.1: Newman runs hand-authored Postman collections. Users
        # build them in Postman / Postwoman / Insomnia and version the
        # exported JSON. Codegen-from-prompt would duplicate that surface
        # for no real win; surface the rationale instead.
        return (
            "newman runner does not author test files — Postman collections "
            "are hand-authored in Postman / Postwoman / Insomnia and exported "
            "as v2.x JSON. Point QA_POSTMAN_COLLECTION at the exported file "
            "and use run_tests to execute the embedded pm.test(...) assertions."
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

    def _collection_path(self) -> str:
        """Read at call-time (not import-time) so tests / users who flip
        env vars between method calls see the new value. Mirrors how
        config.RUNNER_NAME is consulted lazily by get_runner()."""
        path = os.getenv("QA_POSTMAN_COLLECTION", "").strip()
        _validate_collection_path(path)
        return path

    def _base_cmd(self, cli: str, collection_path: str, report_json: Path) -> list[str]:
        """Build the base Newman invocation.

        Flag choices (validated against Newman 6.x — don't repeat the
        v0.6.0 `--report-json` mistake; the right flag is
        `--reporter-json-export`):

          --reporters cli,json            structured output + console
          --reporter-json-export <path>   where to write the JSON report
          -n <count>                      iteration count
          --timeout-request <ms>          per-request timeout
          -e <env.json>                   Postman environment file
          -g <globals.json>               Postman globals file
        """
        env_path = os.getenv("QA_POSTMAN_ENVIRONMENT", "").strip()
        globals_path = os.getenv("QA_POSTMAN_GLOBALS", "").strip()
        try:
            iterations = int(os.getenv("QA_POSTMAN_ITERATIONS", "1") or "1")
        except ValueError:
            iterations = 1
        try:
            timeout_ms = int(os.getenv("QA_POSTMAN_TIMEOUT_REQUEST_MS", "30000") or "30000")
        except ValueError:
            timeout_ms = 30000
        folder_csv = os.getenv("QA_POSTMAN_FOLDER", "").strip()

        cmd: list[str] = [
            cli, "run", collection_path,
            "--reporters", "cli,json",
            "--reporter-json-export", str(report_json),
            "-n", str(iterations),
            "--timeout-request", str(timeout_ms),
        ]
        if env_path:
            cmd.extend(["-e", env_path])
        if globals_path:
            cmd.extend(["-g", globals_path])
        if folder_csv:
            for name in [n.strip() for n in folder_csv.split(",") if n.strip()]:
                cmd.extend(["--folder", name])
        return cmd

    def _walk_items(self, items: list, out: list[str], parent_path: list[str]) -> None:
        """Recursive walk of Postman 2.1 `item[]` arrays. Folders are
        items with a nested `item[]`; leaves have a `request` block.
        We emit `METHOD path :: <name>` per leaf, including the parent
        folder breadcrumb for disambiguation (two requests named "list"
        under different folders should still be distinguishable)."""
        for it in items or []:
            if not isinstance(it, dict):
                continue
            name = it.get("name") or "<unnamed>"
            if "item" in it and isinstance(it["item"], list):
                self._walk_items(it["item"], out, parent_path + [name])
                continue
            req = it.get("request")
            if not req:
                continue
            method = "?"
            path = ""
            if isinstance(req, str):
                method = "GET"
                path = req
            elif isinstance(req, dict):
                method = (req.get("method") or "?").upper()
                url = req.get("url")
                if isinstance(url, str):
                    path = url
                elif isinstance(url, dict):
                    # Postman 2.1 splits URL into raw / host / path.
                    path = url.get("raw") or "/" + "/".join(url.get("path") or [])
            breadcrumb = " / ".join(parent_path) + " :: " if parent_path else ""
            out.append(f"{method} {path} :: {breadcrumb}{name}")

    def _construct_url(self, url_obj) -> str | None:
        """Postman 2.1 URLs can be either a string (`request.url = "https://x/y"`)
        or a struct (`{raw, host, path, query, ...}`). Prefer raw; fall
        back to a host+path join when only the struct is present."""
        if url_obj is None:
            return None
        if isinstance(url_obj, str):
            return url_obj
        if not isinstance(url_obj, dict):
            return None
        raw = url_obj.get("raw")
        if raw:
            return raw
        host = url_obj.get("host")
        path = url_obj.get("path") or []
        if isinstance(host, list):
            host_s = ".".join(host)
        else:
            host_s = host or ""
        path_s = "/" + "/".join(p for p in path if p)
        return (host_s + path_s) if (host_s or path_s) else None

    def _normalize_report(self, report_json: Path) -> None:
        """Translate Newman 6.x JSON into pytest-json-report shape.

        Per-execution + per-assertion mapping:

          run.executions[] × execution.assertions[] →
            one mk-qa-master "test" per assertion (so a single request
            with three pm.test(...) calls becomes three nodeids — same
            granularity as Schemathesis's check breakdown).

          nodeid       = "METHOD <item-name> :: <assertion-name>"
          outcome      = "failed" if assertion.error else "passed"
          duration     = exec.response.responseTime / 1000.0   (ms → seconds)
          longrepr     = redacted assertion error message
          artifacts.request_response.{method, url, request_body,
                                      response_status, response_body,
                                      violation, parent_folder}

        If `run.executions[]` is empty (rare — happens when Newman bails
        before any request runs), fall back to a synthesized empty
        report so downstream paths don't 404.
        """
        empty = {
            "summary": {"total": 0, "passed": 0, "failed": 0, "skipped": 0},
            "duration": 0,
            "tests": [],
        }
        if not report_json.exists():
            self._write_report(empty)
            return
        try:
            data = json.loads(report_json.read_text())
        except (OSError, json.JSONDecodeError):
            self._write_report(empty)
            return

        run = data.get("run") or {}
        executions = run.get("executions") or []
        timings = run.get("timings") or {}

        # Total run duration: prefer `completed - started` (ms), fall back to 0.
        duration_total = 0.0
        try:
            started = timings.get("started") or 0
            completed = timings.get("completed") or 0
            if started and completed and completed >= started:
                duration_total = (completed - started) / 1000.0
        except (TypeError, ValueError):
            pass

        tests: list[dict] = []
        passed = failed = 0

        for ex in executions:
            if not isinstance(ex, dict):
                continue
            item = ex.get("item") or {}
            item_name = item.get("name") or "<unnamed>"
            # Newman puts the parent folder under item.parent or in the
            # cursor's `httpRequestId` lineage; the simplest place is
            # `ex.item.parent` for v6, but it isn't guaranteed. Fall back
            # to None when absent so run_failed degrades to a full rerun.
            parent_folder = None
            parent = item.get("parent")
            if isinstance(parent, dict):
                parent_folder = parent.get("name")
            elif isinstance(parent, str):
                parent_folder = parent

            req = ex.get("request") or {}
            method = (req.get("method") or "?").upper()
            url = self._construct_url(req.get("url"))
            req_body = req.get("body") or {}
            req_body_raw = None
            if isinstance(req_body, dict):
                req_body_raw = req_body.get("raw")
            req_body_redacted = _redact(req_body_raw) if isinstance(req_body_raw, str) else None

            resp = ex.get("response") or {}
            resp_status = resp.get("code")
            # `response.stream` is a Buffer-like {type: "Buffer", data: [bytes...]}.
            # Convert to text if it looks like text; otherwise leave None to
            # avoid spilling binary into the JSON report.
            resp_body_text = self._decode_response_stream(resp.get("stream"))
            resp_body_redacted = _redact(resp_body_text)

            try:
                response_time_ms = float(resp.get("responseTime") or 0)
            except (TypeError, ValueError):
                response_time_ms = 0.0
            duration_s = response_time_ms / 1000.0

            assertions = ex.get("assertions") or []
            if not assertions:
                # Newman ran the request but the user wrote no pm.test(...)
                # for it. Surface as a single "passed" stub so the request
                # still shows up in history / list_tests parity.
                nodeid = f"{method} {item_name} :: (no assertions)"
                tests.append({
                    "nodeid": nodeid,
                    "outcome": "passed",
                    "message": "",
                    "duration": duration_s,
                    "call": {"duration": duration_s, "longrepr": ""},
                    "artifacts": {
                        "request_response": {
                            "method": method,
                            "url": url,
                            "request_body": req_body_redacted,
                            "response_status": resp_status,
                            "response_body": resp_body_redacted,
                            "violation": None,
                            "parent_folder": parent_folder,
                        }
                    },
                })
                passed += 1
                continue

            for a in assertions:
                if not isinstance(a, dict):
                    continue
                a_name = a.get("assertion") or "<unnamed assertion>"
                err = a.get("error")
                if err and isinstance(err, dict):
                    msg = (err.get("message") or "").strip()
                    stack = (err.get("stack") or "")
                    if stack:
                        msg = (msg + " · " + stack[:200]).strip(" ·")
                    longrepr = _redact(msg) or ""
                    outcome = "failed"
                    failed += 1
                else:
                    longrepr = ""
                    outcome = "passed"
                    passed += 1

                nodeid = f"{method} {item_name} :: {a_name}"
                tests.append({
                    "nodeid": nodeid,
                    "outcome": outcome,
                    "message": longrepr,
                    "duration": duration_s,
                    "call": {"duration": duration_s, "longrepr": longrepr},
                    "artifacts": {
                        "request_response": {
                            "method": method,
                            "url": url,
                            "request_body": req_body_redacted,
                            "response_status": resp_status,
                            "response_body": resp_body_redacted,
                            "violation": a_name if outcome == "failed" else None,
                            "parent_folder": parent_folder,
                        }
                    },
                })

        # Fall back to stats-derived summary when we couldn't iterate
        # executions (empty or malformed).
        if not tests:
            stats = run.get("stats") or {}
            assertions_stats = stats.get("assertions") or {}
            requests_stats = stats.get("requests") or {}
            total = int(assertions_stats.get("total") or requests_stats.get("total") or 0)
            f_stat = int(assertions_stats.get("failed") or requests_stats.get("failed") or 0)
            p_stat = max(total - f_stat, 0)
            report = {
                "summary": {
                    "total": total,
                    "passed": p_stat,
                    "failed": f_stat,
                    "skipped": 0,
                },
                "duration": duration_total,
                "tests": [],
            }
            self._write_report(report)
            return

        report = {
            "summary": {
                "total": len(tests),
                "passed": passed,
                "failed": failed,
                "skipped": 0,
            },
            "duration": duration_total,
            "tests": tests,
        }
        self._write_report(report)

    def _decode_response_stream(self, stream) -> str | None:
        """Newman serializes response bodies as Buffer objects:
            {"type": "Buffer", "data": [104, 101, 108, 108, 111, ...]}
        Convert to UTF-8 text when it decodes cleanly. Binary responses
        return None to avoid polluting the JSON report — we'd rather
        lose them than emit malformed escapes."""
        if stream is None:
            return None
        if isinstance(stream, str):
            return stream
        if isinstance(stream, dict):
            data = stream.get("data")
            if isinstance(data, list):
                try:
                    return bytes(b & 0xFF for b in data if isinstance(b, int)).decode(
                        "utf-8", errors="strict"
                    )
                except (UnicodeDecodeError, ValueError):
                    return None
        return None

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
