import importlib.util
import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from .base import TestRunner
from ..config import PROJECT_ROOT, REPORT_PATH, JUNIT_PATH, ARTIFACTS_DIR, HISTORY_DIR


# Detect once at module load — pytest-rerunfailures lets us auto-retry transient
# failures so the optimizer's flake signal is grounded in repeat-confirmed fails.
_HAS_RERUNFAILURES = importlib.util.find_spec("pytest_rerunfailures") is not None


TEST_TEMPLATE = '''"""
{description}
"""
from playwright.sync_api import Page, expect


def test_{slug}(page: Page):
    # TODO: 由 Claude 補完實作
    page.goto("https://example.com")
    expect(page).to_have_title("Example Domain")
'''


# Sample values keyed by input type — used when smart-generating from an
# analyze_url form module so the resulting test is runnable without manual fills.
_SAMPLE_VALUES = {
    "email": "test@example.com",
    "password": "TestPass123!",
    "tel": "0912345678",
    "phone": "0912345678",
    "url": "https://example.com",
    "number": "1",
    "search": "test query",
    "text": "test value",
    "textarea": "Sample input",
    "date": "2026-01-01",
}


class PytestPlaywrightRunner(TestRunner):
    name = "pytest-playwright"

    def list_tests(self) -> str:
        result = subprocess.run(
            ["pytest", "--collect-only", "-q"],
            cwd=PROJECT_ROOT, capture_output=True, text=True,
        )
        return result.stdout or result.stderr

    def _base_cmd(self, browser: str) -> list[str]:
        cmd = [
            "pytest",
            f"--browser={browser}",
            "--screenshot=only-on-failure",
            "--video=retain-on-failure",
            "--tracing=retain-on-failure",
            f"--output={ARTIFACTS_DIR}",
            "--json-report",
            f"--json-report-file={REPORT_PATH}",
            f"--junitxml={JUNIT_PATH}",
        ]
        if _HAS_RERUNFAILURES:
            cmd += ["--reruns", "1", "--reruns-delay", "0"]
        return cmd

    def run_tests(self, filter=None, **kwargs) -> dict:
        cmd = self._base_cmd(kwargs.get("browser", "chromium"))
        if kwargs.get("headed"):
            cmd.append("--headed")
        if filter:
            cmd.extend(["-k", filter])

        result = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True)
        self._archive_report()
        return {
            "exit_code": result.returncode,
            "stdout_tail": result.stdout[-2000:],
            "stderr_tail": result.stderr[-1000:],
            "retry_enabled": _HAS_RERUNFAILURES,
        }

    def run_failed(self) -> dict:
        cmd = self._base_cmd("chromium") + ["--lf"]
        result = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True)
        self._archive_report()
        return {
            "exit_code": result.returncode,
            "stdout_tail": result.stdout[-2000:],
            "retry_enabled": _HAS_RERUNFAILURES,
        }

    def get_report_summary(self) -> dict:
        if not REPORT_PATH.exists():
            return {"error": "找不到報告，請先執行 run_tests"}
        data = json.loads(REPORT_PATH.read_text())
        summary = data.get("summary", {})
        # Count tests that needed a retry to pass — flake-in-this-run signal.
        # pytest-rerunfailures emits two records for the same nodeid: first a
        # "rerun" outcome, then the final outcome.
        seen_rerun: set[str] = set()
        flaky_in_run = 0
        for t in data.get("tests", []) or []:
            nodeid = t.get("nodeid")
            if t.get("outcome") == "rerun" and nodeid:
                seen_rerun.add(nodeid)
            elif t.get("outcome") == "passed" and nodeid in seen_rerun:
                flaky_in_run += 1
        return {
            "total": summary.get("total", 0),
            "passed": summary.get("passed", 0),
            "failed": summary.get("failed", 0),
            "skipped": summary.get("skipped", 0),
            "flaky_in_run": flaky_in_run,
            "duration": data.get("duration"),
        }

    def get_failure_details(self, test_id=None) -> list[dict]:
        if not REPORT_PATH.exists():
            return [{"error": "找不到報告"}]
        data = json.loads(REPORT_PATH.read_text())
        failures = [t for t in data.get("tests", []) if t.get("outcome") == "failed"]
        if test_id:
            failures = [t for t in failures if test_id in t.get("nodeid", "")]
        return [
            {
                "nodeid": t["nodeid"],
                "message": t.get("call", {}).get("longrepr", ""),
                "duration": t.get("call", {}).get("duration"),
                **self._find_artifacts(t["nodeid"]),
            }
            for t in failures
        ]

    def _find_artifacts(self, nodeid: str) -> dict:
        """Best-effort lookup for failure artifacts (screenshot/trace/video).

        Why: playwright-pytest saves artifacts into a per-test folder whose name
        is a sanitized version of the nodeid + browser. Match by function name
        token after `::` to stay resilient to its internal slug rules.
        """
        out: dict[str, str | None] = {"screenshot": None, "trace": None, "video": None}
        if not ARTIFACTS_DIR.exists():
            return out
        test_func = nodeid.split("::")[-1]
        token = re.sub(r"[^\w]+", "-", test_func).strip("-").lower()
        if not token:
            return out
        for folder in ARTIFACTS_DIR.iterdir():
            if not folder.is_dir() or folder.name == "history":
                continue
            if token not in folder.name.lower():
                continue
            for png in sorted(folder.glob("*.png")):
                out["screenshot"] = str(png)
                break
            for tz in sorted(folder.glob("trace*.zip")):
                out["trace"] = str(tz)
                break
            for v in sorted(folder.glob("*.webm")):
                out["video"] = str(v)
                break
            break
        return out

    def _archive_report(self) -> None:
        """Snapshot report.json + write a fresh optimization-plan.md.

        Why: the optimizer's whole value is being up-to-date right after the run
        finishes. Hooking here means callers don't have to remember an extra step.
        Lazy import avoids a circular dependency at module-load time.
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
            # Optimizer failures should never block test running.
            pass

    def get_history(self, limit: int = 10) -> list[dict]:
        if not HISTORY_DIR.exists():
            return []
        files = sorted(HISTORY_DIR.glob("*.json"), reverse=True)[:limit]
        history: list[dict] = []
        for f in reversed(files):  # oldest first for natural trend reading
            try:
                data = json.loads(f.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            summary = data.get("summary", {}) or {}
            total = summary.get("total", 0) or 0
            passed = summary.get("passed", 0) or 0
            history.append({
                "file": f.name,
                "timestamp": f.stem,
                "total": total,
                "passed": passed,
                "failed": summary.get("failed", 0) or 0,
                "skipped": summary.get("skipped", 0) or 0,
                "duration": data.get("duration"),
                "pass_rate": (passed / total * 100) if total else 0,
            })
        return history

    def generate_test(
        self,
        description: str,
        filename: str,
        url: str | None = None,
        module: dict | None = None,
    ) -> str:
        if not filename.startswith("test_"):
            filename = f"test_{filename}"
        if not filename.endswith(".py"):
            filename += ".py"
        slug = filename.replace("test_", "").replace(".py", "")

        # If caller hands us a module from analyze_url, render a runnable
        # skeleton with concrete selectors instead of a `# TODO` stub.
        if isinstance(module, dict) and module.get("kind") == "form":
            content = self._render_form_test(description, slug, url, module)
        elif isinstance(module, dict):
            content = self._render_generic_module_test(description, slug, url, module)
        else:
            content = TEST_TEMPLATE.format(description=description, slug=slug)

        target = PROJECT_ROOT / filename
        target.write_text(content)
        return f"已產生 {target}，內容：\n\n{content}"

    def _render_form_test(self, description: str, slug: str, url: str | None, module: dict) -> str:
        sel = module.get("selectors") or {}
        fields = sel.get("fields") or []
        submit = sel.get("submit")
        fill_lines: list[str] = []
        for f in fields:
            s = f.get("selector")
            if not s:
                continue
            kind = (f.get("type") or "").lower()
            if kind == "select":
                fill_lines.append(f"    page.locator({s!r}).select_option(index=1)")
            elif kind in ("checkbox", "radio"):
                fill_lines.append(f"    page.locator({s!r}).check()")
            else:
                value = _SAMPLE_VALUES.get(kind, "test value")
                fill_lines.append(f"    page.locator({s!r}).fill({value!r})")
        fills_body = "\n".join(fill_lines) if fill_lines else "    # No fillable fields detected"
        submit_body = (
            f"    page.locator({submit!r}).click()" if submit
            else "    # No submit button detected"
        )
        tcs = module.get("candidate_tcs") or []
        tc_block = "\n".join(f"    # TC: {tc}" for tc in tcs[:3])
        goto_url = url or "https://example.com"
        return (
            '"""\n'
            f"{description}\n\n"
            f'Auto-generated from analyze_url module: {module.get("name", "(unnamed)")} (kind=form)\n'
            '"""\n'
            "from playwright.sync_api import Page, expect\n\n\n"
            f"def test_{slug}(page: Page):\n"
            f"    page.goto({goto_url!r})\n"
            f"{fills_body}\n"
            f"{submit_body}\n"
            + (f"{tc_block}\n" if tc_block else "")
            + "    # TODO: 補上實際斷言，例如：\n"
              "    # expect(page).to_have_url(...)\n"
              '    # expect(page.get_by_text("成功")).to_be_visible()\n'
        )

    def _render_generic_module_test(self, description: str, slug: str, url: str | None, module: dict) -> str:
        kind = module.get("kind", "unknown")
        sel = module.get("selectors") or {}
        target_sel = sel.get("container") or sel.get("trigger") or "body"
        tcs = module.get("candidate_tcs") or []
        tc_block = "\n".join(f"    # TC: {tc}" for tc in tcs[:3])
        goto_url = url or "https://example.com"
        return (
            '"""\n'
            f"{description}\n\n"
            f'Auto-generated from analyze_url module: {module.get("name", "(unnamed)")} (kind={kind})\n'
            '"""\n'
            "from playwright.sync_api import Page, expect\n\n\n"
            f"def test_{slug}(page: Page):\n"
            f"    page.goto({goto_url!r})\n"
            f"    target = page.locator({target_sel!r})\n"
            "    expect(target).to_be_visible()\n"
            + (f"{tc_block}\n" if tc_block else "")
            + "    # TODO: 補上實際互動與斷言\n"
        )

    def codegen(self, url: str, output: str = "recorded_test.py") -> str:
        target = PROJECT_ROOT / output
        subprocess.run(
            ["playwright", "codegen", "-o", str(target), url],
            cwd=PROJECT_ROOT,
        )
        return f"錄製完成，已存至 {target}"
