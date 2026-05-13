"""Maestro runner — declarative YAML mobile flows, cross iOS + Android.

Why Maestro as the first mobile runner: one YAML test runs unchanged on iOS
simulator, Android emulator, and real devices. The DSL is small enough that
generate_test can produce a reasonable skeleton from a description without
needing platform-specific Swift / Kotlin knowledge.

Output mapping: `maestro test --format junit --output junit.xml` emits a
standard JUnit XML. We convert it into the same `report.json` shape that
pytest-json-report produces, so every downstream consumer (optimizer,
history, HTML reporter, get_test_history, get_failure_details) keeps
working without runner-specific code paths.

What this PoC covers:
  - list_tests / run_tests / run_failed / get_report_summary
  - get_failure_details with best-effort screenshot lookup
  - get_all_test_details (for HTML reporter parity)
  - generate_test (basic Maestro YAML skeleton; mobile module-aware
    smart-generation can come later)
  - get_history (history archive feeds the optimizer)
  - _archive_report → triggers optimizer.write_plan() like pytest does

What's intentionally out of scope for v0:
  - analyze_screen (mobile equivalent of analyze_url) — needs Maestro
    `hierarchy` command parsing
  - auto-retry of flaky flows — Maestro has no native --reruns
  - Per-step trace extraction — Maestro recordings are video, not
    structured step lists
"""
import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from .base import TestRunner
from ..config import (
    PROJECT_ROOT,
    REPORT_PATH,
    JUNIT_PATH,
    ARTIFACTS_DIR,
    HISTORY_DIR,
)


# Default skeleton when the caller has no module info. Mobile equivalent of
# pytest-playwright's TEST_TEMPLATE — minimal but runnable on `maestro test`.
FLOW_TEMPLATE = """# {description}
appId: {app_id}
---
- launchApp
# TODO: 由 Claude 補完實作
- assertVisible: "Welcome"
"""


class MaestroRunner(TestRunner):
    name = "maestro"

    # ---- public TestRunner API --------------------------------------------

    def list_tests(self) -> str:
        flows = sorted(p for p in PROJECT_ROOT.rglob("*.yaml") if "test-results" not in p.parts)
        if not flows:
            return f"(no .yaml flows found under {PROJECT_ROOT})"
        return "\n".join(str(f.relative_to(PROJECT_ROOT)) for f in flows)

    def run_tests(self, filter=None, **kwargs) -> dict:
        flows = self._discover_flows(filter)
        if not flows:
            return {"error": f"no Maestro flows match filter={filter!r}"}
        cmd = self._base_cmd() + [str(f) for f in flows]
        result = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True)
        self._junit_to_report_json()
        retried = self._retry_failures_if_any()
        self._archive_report()
        # Adjust exit_code: maestro returned 1 because some flows failed on
        # the first attempt; after retry they may have passed. Source of
        # truth is the patched report.json.
        post_summary = self.get_report_summary()
        post_failed = (post_summary.get("failed") or 0) if isinstance(post_summary, dict) else 0
        adjusted_exit = 0 if post_failed == 0 else 1
        return {
            "exit_code": adjusted_exit,
            "raw_exit_code": result.returncode,
            "flows_run": len(flows),
            "retry_enabled": self._retry_enabled(),
            "flaky_in_run": retried,
            "stdout_tail": result.stdout[-2000:],
            "stderr_tail": result.stderr[-1000:],
        }

    def run_failed(self) -> dict:
        if not REPORT_PATH.exists():
            return {"error": "no previous report.json; run tests first"}
        try:
            data = json.loads(REPORT_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            return {"error": "report.json invalid or unreadable"}
        # nodeid in our converted shape looks like "classname::name"; for
        # Maestro classname == flow file path, name == top-level flow name.
        # We re-run the underlying files.
        failed_files: list[Path] = []
        for t in data.get("tests", []) or []:
            if t.get("outcome") != "failed":
                continue
            nodeid = t.get("nodeid") or ""
            path_part = nodeid.split("::")[0]
            if path_part:
                p = (PROJECT_ROOT / path_part) if not path_part.startswith("/") else Path(path_part)
                if p.is_file():
                    failed_files.append(p)
        if not failed_files:
            return {"info": "no previous failures to re-run"}
        cmd = self._base_cmd() + [str(p) for p in failed_files]
        result = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True)
        self._junit_to_report_json()
        retried = self._retry_failures_if_any()
        self._archive_report()
        post_summary = self.get_report_summary()
        post_failed = (post_summary.get("failed") or 0) if isinstance(post_summary, dict) else 0
        adjusted_exit = 0 if post_failed == 0 else 1
        return {
            "exit_code": adjusted_exit,
            "raw_exit_code": result.returncode,
            "flows_rerun": len(failed_files),
            "retry_enabled": self._retry_enabled(),
            "flaky_in_run": retried,
            "stdout_tail": result.stdout[-2000:],
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
            # Auto-retry: number of flows that initially failed but passed
            # on the second attempt (Maestro has no native --reruns; we add
            # this via _retry_failures_if_any). HTML reporter shows a FLAKY
            # badge when > 0.
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
        out: list[dict] = []
        for t in failures:
            flow_path = self._flow_path_for(t["nodeid"])
            out.append({
                "nodeid": t["nodeid"],
                "title": self._flow_title(flow_path),
                "message": t.get("message", ""),
                "duration": t.get("duration"),
                "steps": self._flow_steps(flow_path),
                "screenshot": self._find_screenshot(t["nodeid"]),
                "trace": None,   # Maestro produces video recordings, not traces
                "video": self._find_video(t["nodeid"]),
            })
        return out

    def get_all_test_details(self) -> list[dict]:
        """Per-test rows for the HTML reporter (pass + fail in one pass)."""
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
            flow_path = self._flow_path_for(nodeid)
            out.append({
                "nodeid": nodeid,
                "title": self._flow_title(flow_path),
                "outcome": outcome,
                "duration": t.get("duration"),
                "message": t.get("message", "") if outcome == "failed" else "",
                "steps": self._flow_steps(flow_path),
                "screenshot": self._find_screenshot(nodeid),
                "trace": None,
                "video": self._find_video(nodeid),
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
        """Produce a Maestro flow YAML.

        When `module` is supplied (from analyze_screen), we render a
        kind-specific skeleton that's ready to run — tapOn / inputText /
        takeScreenshot chains derived from the module's selectors —
        instead of a generic `# TODO` stub.

        `url` is reused as `appId` in mobile context (bundle id /
        package name).
        """
        if not filename.endswith(".yaml"):
            filename += ".yaml"
        app_id = url or "com.example.app"

        if isinstance(module, dict):
            kind = module.get("kind")
            if kind == "cta":
                body = self._render_cta_flow(description, app_id, module, business_context)
            elif kind == "form":
                body = self._render_form_flow(description, app_id, module, business_context)
            elif kind == "tab_bar":
                body = self._render_tab_bar_flow(description, app_id, module, business_context)
            else:
                body = self._render_generic_module_flow(description, app_id, module, business_context)
        else:
            body = self._render_basic_flow(description, app_id, business_context)

        target = PROJECT_ROOT / filename
        target.write_text(body, encoding="utf-8")
        return f"已產生 {target}，內容：\n\n{body}"

    def _flow_header(
        self,
        description: str,
        app_id: str,
        business_context: str | None,
        source_info: str = "",
    ) -> str:
        """Common YAML header: comment block + appId + `---` separator.

        Description comment becomes the case name via the existing
        _flow_title parser; business_context goes as a leading `#` block
        below it so reviewers see the rationale inline.
        """
        lines = [f"# {description}"]
        if source_info:
            lines.append(f"# {source_info}")
        if business_context and str(business_context).strip():
            lines.append("# Business context:")
            for raw in str(business_context).strip().splitlines():
                lines.append(f"# {raw.rstrip()}" if raw.strip() else "#")
        lines.append(f"appId: {app_id}")
        lines.append("---")
        return "\n".join(lines)

    def _tap_block(self, label: str, resource_id: str | None) -> str:
        """Prefer `id:` (resource-id / accessibilityIdentifier) over `text`.

        Maestro matches both; id is stabler against copy / localization
        changes. Falls back to text when no id is exposed.
        """
        if resource_id:
            return f"- tapOn:\n    id: {resource_id!r}"
        return f"- tapOn: {label!r}"

    def _sample_input_value(self, label: str) -> str:
        """Pick a reasonable test value from a field label.

        Maestro's `inputText` accepts any string; we bias toward
        domain-typical placeholders so the generated flow doesn't need
        manual editing for happy-path runs.
        """
        lower = (label or "").lower()
        if any(k in lower for k in ("email", "信箱", "電子郵件")):
            return "test@example.com"
        if any(k in lower for k in ("password", "密碼")):
            return "TestPass123!"
        if any(k in lower for k in ("phone", "電話", "手機", "tel")):
            return "0912345678"
        if any(k in lower for k in ("name", "姓名", "暱稱")):
            return "QA Tester"
        if any(k in lower for k in ("search", "搜尋")):
            return "test query"
        if any(k in lower for k in ("number", "數字", "數量", "amount")):
            return "1"
        return "test"

    def _render_cta_flow(
        self, description: str, app_id: str, module: dict, business_context: str | None,
    ) -> str:
        sel = module.get("selectors") or {}
        label = sel.get("text") or module.get("name") or "(unlabeled)"
        rid = sel.get("resource_id")
        tcs = (module.get("candidate_tcs") or [])[:3]
        name = module.get("name") or "cta"
        safe = re.sub(r"[^\w]+", "_", name.lower()).strip("_") or "cta"
        header = self._flow_header(
            description, app_id, business_context,
            f"Auto-generated from analyze_screen module: {name} (kind=cta)",
        )
        tc_block = "\n".join(f"# TC: {tc}" for tc in tcs)
        return (
            f"{header}\n"
            "- launchApp:\n"
            "    clearState: false\n"
            "- waitForAnimationToEnd:\n"
            "    timeout: 5000\n"
            f"{self._tap_block(label, rid)}\n"
            "- waitForAnimationToEnd:\n"
            "    timeout: 3000\n"
            f"- takeScreenshot: after_{safe}\n"
            + (f"{tc_block}\n" if tc_block else "")
            + "# TODO: 補上 assertVisible 或其他預期結果（成功 / 導頁 / modal 等）\n"
        )

    def _render_form_flow(
        self, description: str, app_id: str, module: dict, business_context: str | None,
    ) -> str:
        sel = module.get("selectors") or {}
        fields = sel.get("fields") or []
        tcs = (module.get("candidate_tcs") or [])[:3]
        name = module.get("name") or "form"
        safe = re.sub(r"[^\w]+", "_", name.lower()).strip("_") or "form"
        header = self._flow_header(
            description, app_id, business_context,
            f"Auto-generated from analyze_screen module: {name} (kind=form)",
        )
        fill_lines: list[str] = []
        for f in fields[:8]:
            field_label = (f.get("label") or f.get("hint") or "field").strip()
            rid = f.get("resource_id")
            value = self._sample_input_value(field_label)
            fill_lines.append(self._tap_block(field_label, rid))
            fill_lines.append(f"- inputText: {value!r}")
        fills = "\n".join(fill_lines) if fill_lines else "# No fillable fields detected"
        tc_block = "\n".join(f"# TC: {tc}" for tc in tcs)
        return (
            f"{header}\n"
            "- launchApp:\n"
            "    clearState: false\n"
            "- waitForAnimationToEnd:\n"
            "    timeout: 5000\n"
            f"{fills}\n"
            f"- takeScreenshot: filled_{safe}\n"
            + (f"{tc_block}\n" if tc_block else "")
            + "# TODO: 補上送出按鈕（tapOn: \"送出\" 之類）+ 預期成功 / 錯誤訊息驗證\n"
        )

    def _render_tab_bar_flow(
        self, description: str, app_id: str, module: dict, business_context: str | None,
    ) -> str:
        tabs = module.get("tabs") or []
        tcs = (module.get("candidate_tcs") or [])[:3]
        name = module.get("name") or "tabs"
        header = self._flow_header(
            description, app_id, business_context,
            f"Auto-generated from analyze_screen module: {name} (kind=tab_bar)",
        )
        tap_lines: list[str] = []
        for t in tabs[:5]:
            label = (t.get("label") or "").strip()
            if not label:
                continue
            safe = re.sub(r"[^\w]+", "_", label.lower()).strip("_") or "tab"
            tap_lines.append(f"- tapOn: {label!r}")
            tap_lines.append("- waitForAnimationToEnd:\n    timeout: 3000")
            tap_lines.append(f"- takeScreenshot: tab_{safe}")
        taps = "\n".join(tap_lines) if tap_lines else "# No tabs detected"
        tc_block = "\n".join(f"# TC: {tc}" for tc in tcs)
        return (
            f"{header}\n"
            "- launchApp:\n"
            "    clearState: false\n"
            "- waitForAnimationToEnd:\n"
            "    timeout: 5000\n"
            f"{taps}\n"
            + (f"{tc_block}\n" if tc_block else "")
            + "# TODO: 切換每個 tab 後 assertVisible 對應頁面的代表元素\n"
        )

    def _render_generic_module_flow(
        self, description: str, app_id: str, module: dict, business_context: str | None,
    ) -> str:
        name = module.get("name") or "screen"
        kind = module.get("kind") or "unknown"
        tcs = (module.get("candidate_tcs") or [])[:3]
        safe = re.sub(r"[^\w]+", "_", name.lower()).strip("_") or "screen"
        header = self._flow_header(
            description, app_id, business_context,
            f"Auto-generated from analyze_screen module: {name} (kind={kind})",
        )
        tc_block = "\n".join(f"# TC: {tc}" for tc in tcs)
        return (
            f"{header}\n"
            "- launchApp:\n"
            "    clearState: false\n"
            "- waitForAnimationToEnd:\n"
            "    timeout: 5000\n"
            f"- takeScreenshot: {safe}\n"
            + (f"{tc_block}\n" if tc_block else "")
            + "# TODO: 補上實際互動與斷言\n"
        )

    def _render_basic_flow(
        self, description: str, app_id: str, business_context: str | None,
    ) -> str:
        header = self._flow_header(description, app_id, business_context)
        return (
            f"{header}\n"
            "- launchApp\n"
            "# TODO: 由 Claude 補完實作\n"
            '- assertVisible: "Welcome"\n'
        )

    def codegen(self, url: str, output: str = "recorded_flow.yaml") -> str:
        """Maestro Studio records a flow interactively; this is a hint, not a wrapper."""
        return (
            f"Maestro 沒有 codegen 子命令，但 `maestro studio` 提供互動式錄製。"
            f" 請手動執行 `maestro studio` 後另存為 {PROJECT_ROOT / output}。"
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

    def _base_cmd(self) -> list[str]:
        ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        return [
            "maestro", "test",
            "--format", "junit",
            "--output", str(JUNIT_PATH),
            "--debug-output", str(ARTIFACTS_DIR),
        ]

    def _discover_flows(self, filter: str | None) -> list[Path]:
        flows = sorted(p for p in PROJECT_ROOT.rglob("*.yaml") if "test-results" not in p.parts)
        if filter:
            f = filter.lower()
            flows = [p for p in flows if f in str(p.relative_to(PROJECT_ROOT)).lower()]
        return flows

    def _junit_to_report_json(self) -> None:
        """Translate Maestro's JUnit XML into our pytest-shaped report.json.

        Why this shape: optimizer + html reporter + history all expect the
        pytest-json-report keys (summary.total/passed/failed/skipped, tests[]
        with outcome / call.duration / call.longrepr). Doing the translation
        here keeps the rest of the codebase runner-agnostic.
        """
        if not JUNIT_PATH.exists():
            return
        try:
            root = ET.parse(JUNIT_PATH).getroot()
        except (OSError, ET.ParseError):
            return

        tests: list[dict] = []
        passed = failed = skipped = 0
        duration_total = 0.0
        for tc in root.iter("testcase"):
            name = tc.get("name") or ""
            classname = tc.get("classname") or ""
            nodeid = f"{classname}::{name}" if classname else name
            try:
                dur = float(tc.get("time") or 0)
            except ValueError:
                dur = 0.0
            duration_total += dur

            failure_node = tc.find("failure")
            error_node = tc.find("error")
            skipped_node = tc.find("skipped")

            if failure_node is not None or error_node is not None:
                outcome = "failed"
                failed += 1
                src = failure_node if failure_node is not None else error_node
                msg = (src.get("message") or "").strip()
                body = (src.text or "").strip()
                full = (msg + ("\n" + body if body else "")).strip()
            elif skipped_node is not None:
                outcome = "skipped"
                skipped += 1
                full = ""
            else:
                outcome = "passed"
                passed += 1
                full = ""

            tests.append({
                "nodeid": nodeid,
                "outcome": outcome,
                "message": full,
                "duration": dur,
                "call": {"duration": dur, "longrepr": full},
            })

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
        REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def _retry_enabled(self) -> bool:
        """Auto-retry is on by default; opt out via MAESTRO_RETRY=false.

        Mirrors pytest_playwright's behavior (which auto-enables when
        pytest-rerunfailures is installed) but applies to all Maestro
        runs since Maestro itself has no rerun flag.
        """
        return os.getenv("MAESTRO_RETRY", "true").lower() not in ("false", "0", "no")

    def _retry_failures_if_any(self) -> int:
        """Re-run any flow that failed on the first attempt; patch report.json.

        Maestro lacks `--reruns`. We translate JUnit → report.json on the
        first pass, then if any tests failed AND retry is enabled, we
        re-invoke `maestro test` on just those flow files, parse the
        retry JUnit, and patch report.json so pass-on-retry entries flip
        outcome from failed → passed and increment summary.flaky_in_run.

        Returns the number of flows that flipped (== flaky-in-run count).
        Bails out silently on any error so a retry mishap can't block
        the main run from reporting.
        """
        if not self._retry_enabled():
            return 0
        if not REPORT_PATH.exists():
            return 0
        try:
            data = json.loads(REPORT_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            return 0
        failed = [t for t in (data.get("tests") or []) if t.get("outcome") == "failed"]
        if not failed:
            return 0

        retry_flows: list[Path] = []
        for t in failed:
            nodeid = t.get("nodeid") or ""
            p = self._flow_path_for(nodeid)
            if p and p not in retry_flows:
                retry_flows.append(p)
        if not retry_flows:
            return 0

        # Write retry JUnit to a separate path so we don't trash the original.
        retry_junit = ARTIFACTS_DIR / "junit-retry.xml"
        try:
            ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        except OSError:
            return 0
        cmd = [
            "maestro", "test",
            "--format", "junit",
            "--output", str(retry_junit),
            "--debug-output", str(ARTIFACTS_DIR),
        ] + [str(p) for p in retry_flows]
        try:
            subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True)
        except OSError:
            return 0
        if not retry_junit.is_file():
            return 0

        try:
            root = ET.parse(retry_junit).getroot()
        except (OSError, ET.ParseError):
            return 0

        # Build {nodeid: outcome} for the retry attempt.
        retry_outcomes: dict[str, str] = {}
        for tc in root.iter("testcase"):
            name = tc.get("name") or ""
            classname = tc.get("classname") or ""
            nid = f"{classname}::{name}" if classname else name
            if tc.find("failure") is not None or tc.find("error") is not None:
                retry_outcomes[nid] = "failed"
            else:
                retry_outcomes[nid] = "passed"

        # Patch the main report.json in place.
        flipped = 0
        for t in (data.get("tests") or []):
            if t.get("outcome") != "failed":
                continue
            nid = t.get("nodeid")
            if not nid or retry_outcomes.get(nid) != "passed":
                continue
            t["outcome"] = "passed"
            t["was_flaky_in_run"] = True
            # Clear the failure message so the HTML reporter doesn't keep
            # showing it as a failed-card; keep it on a debug field.
            t["original_failure_message"] = t.get("message", "")
            t["message"] = ""
            (t.setdefault("call", {}))["longrepr"] = ""
            flipped += 1

        if flipped:
            s = data.setdefault("summary", {})
            s["failed"] = sum(1 for t in (data.get("tests") or []) if t.get("outcome") == "failed")
            s["passed"] = sum(1 for t in (data.get("tests") or []) if t.get("outcome") == "passed")
            s["flaky_in_run"] = (s.get("flaky_in_run") or 0) + flipped
            try:
                REPORT_PATH.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8",
                )
            except OSError:
                return 0
        return flipped

    def _archive_report(self) -> None:
        """Snapshot report.json + ask optimizer to refresh the plan.

        Mirrors pytest_playwright._archive_report so history + optimization
        cadence is identical across runners.
        """
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
            # Optimizer failure must never block test runs.
            pass

    def _find_screenshot(self, nodeid: str) -> str | None:
        # 1. Failure auto-screenshots may land under --debug-output; check first.
        legacy = self._find_artifact(nodeid, ("*.png",))
        if legacy:
            return legacy
        # 2. Named `takeScreenshot: <name>` writes <name>.png at cwd (PROJECT_ROOT).
        #    Parse the source flow YAML to recover those names + match the file.
        shots = self._flow_screenshots(self._flow_path_for(nodeid))
        return str(shots[0]) if shots else None

    def _find_video(self, nodeid: str) -> str | None:
        return self._find_artifact(nodeid, ("*.mp4", "*.webm"))

    def _flow_path_for(self, nodeid: str) -> Path | None:
        """Find the source .yaml file for a given Maestro test nodeid.

        Maestro's JUnit emits nodeid as `<classname>::<name>` where both are
        usually the flow name (no path). We rglob PROJECT_ROOT for a matching
        .yaml stem. Skips test-results/ so we don't match archived snapshots.
        """
        name = nodeid.split("::")[-1]
        if not name:
            return None
        for p in PROJECT_ROOT.rglob("*.yaml"):
            if "test-results" in p.parts:
                continue
            if p.stem == name:
                return p
        return None

    def _flow_title(self, flow_path: Path | None) -> str | None:
        """Pull the leading `#` comment block from a YAML flow as a case name.

        Convention used by Maestro authors: opening lines like `# Smoke: app
        starts and shows home` describe what the flow does. We grab the
        consecutive `#` lines before the first directive and join them with
        ' / '. Returns None when there's no leading comment.
        """
        if not flow_path or not flow_path.is_file():
            return None
        try:
            lines = flow_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        parts: list[str] = []
        for line in lines:
            s = line.strip()
            if s.startswith("#"):
                parts.append(s.lstrip("#").strip())
            elif not s:
                if parts:
                    break  # blank line after comments = end of title block
                continue
            else:
                break
        title = " / ".join(p for p in parts if p)
        return title or None

    def _flow_steps(self, flow_path: Path | None) -> list[dict]:
        """Extract top-level Maestro actions from a flow YAML as step entries.

        Match the shape the HTML reporter expects (same as
        pytest_playwright._extract_steps): list of {api, title}. Only
        top-level `-` actions are captured; nested commands inside
        `runFlow.commands:` are skipped to keep the step list focused on
        the flow's outline. Pure regex (no PyYAML dependency).
        """
        if not flow_path or not flow_path.is_file():
            return []
        try:
            text = flow_path.read_text(encoding="utf-8")
        except OSError:
            return []
        # Maestro YAML separates header (appId, name) from steps with `---`.
        # Steps come after; if no separator, treat the whole file as steps.
        body = text.split("---", 1)[1] if "---" in text else text
        # `^-\s+...` — anchored at column 0 so indented sub-actions (e.g.
        # nested under runFlow.commands) don't get pulled up as top-level.
        step_re = re.compile(r"^-\s+(\w+):?\s*(.*?)\s*$")
        steps: list[dict] = []
        for line in body.splitlines():
            m = step_re.match(line)
            if not m:
                continue
            action = m.group(1)
            value = m.group(2).strip()
            # title left empty when the action has no inline arg (e.g. `- launchApp:`)
            # so the HTML reporter shows the api alone without duplicating it.
            steps.append({
                "api": action,
                "title": value,
            })
        return steps

    def _flow_screenshots(self, flow_path: Path | None) -> list[Path]:
        """Locate PNGs that the flow's `takeScreenshot: <name>` directives produced.

        Maestro saves named screenshots at cwd (PROJECT_ROOT), not under
        --debug-output. Parse the YAML for the directive's name token and
        check for `<name>.png` at PROJECT_ROOT.
        """
        if not flow_path or not flow_path.is_file():
            return []
        try:
            text = flow_path.read_text(encoding="utf-8")
        except OSError:
            return []
        names = re.findall(r'takeScreenshot:\s*"?([\w\-]+)"?', text)
        found: list[Path] = []
        for n in names:
            p = PROJECT_ROOT / f"{n}.png"
            if p.is_file():
                found.append(p)
        return found

    def _find_artifact(self, nodeid: str, patterns: tuple[str, ...]) -> str | None:
        """Best-effort match: token of the flow filename appears in folder name.

        Maestro's --debug-output layout varies across versions; we scan
        recursively and pick the first file matching any of the patterns.
        """
        if not ARTIFACTS_DIR.exists():
            return None
        flow_part = nodeid.split("::")[-1]
        flow_stem = Path(flow_part).stem or flow_part
        token = re.sub(r"[^a-z0-9]+", "-", flow_stem.lower()).strip("-")
        if not token:
            return None
        for folder in ARTIFACTS_DIR.rglob("*"):
            if not folder.is_dir() or folder.name == "history":
                continue
            if token not in folder.name.lower():
                continue
            for pat in patterns:
                hits = sorted(folder.glob(pat))
                if hits:
                    return str(hits[0])
        return None
