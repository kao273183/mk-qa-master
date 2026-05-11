import json
import subprocess
from .base import TestRunner
from ..config import PROJECT_ROOT, REPORT_PATH


TEST_TEMPLATE = '''"""
{description}
"""
from playwright.sync_api import Page, expect


def test_{slug}(page: Page):
    # TODO: 由 Claude 補完實作
    page.goto("https://example.com")
    expect(page).to_have_title("Example Domain")
'''


class PytestPlaywrightRunner(TestRunner):
    name = "pytest-playwright"

    def list_tests(self) -> str:
        result = subprocess.run(
            ["pytest", "--collect-only", "-q"],
            cwd=PROJECT_ROOT, capture_output=True, text=True,
        )
        return result.stdout or result.stderr

    def run_tests(self, filter=None, **kwargs) -> dict:
        cmd = [
            "pytest",
            f"--browser={kwargs.get('browser', 'chromium')}",
            "--json-report",
            f"--json-report-file={REPORT_PATH}",
        ]
        if kwargs.get("headed"):
            cmd.append("--headed")
        if filter:
            cmd.extend(["-k", filter])

        result = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True)
        return {
            "exit_code": result.returncode,
            "stdout_tail": result.stdout[-2000:],
            "stderr_tail": result.stderr[-1000:],
        }

    def run_failed(self) -> dict:
        result = subprocess.run(
            ["pytest", "--lf", "--json-report", f"--json-report-file={REPORT_PATH}"],
            cwd=PROJECT_ROOT, capture_output=True, text=True,
        )
        return {"exit_code": result.returncode, "stdout_tail": result.stdout[-2000:]}

    def get_report_summary(self) -> dict:
        if not REPORT_PATH.exists():
            return {"error": "找不到報告，請先執行 run_tests"}
        data = json.loads(REPORT_PATH.read_text())
        summary = data.get("summary", {})
        return {
            "total": summary.get("total", 0),
            "passed": summary.get("passed", 0),
            "failed": summary.get("failed", 0),
            "skipped": summary.get("skipped", 0),
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
            }
            for t in failures
        ]

    def generate_test(self, description: str, filename: str) -> str:
        if not filename.startswith("test_"):
            filename = f"test_{filename}"
        if not filename.endswith(".py"):
            filename += ".py"
        slug = filename.replace("test_", "").replace(".py", "")
        content = TEST_TEMPLATE.format(description=description, slug=slug)
        target = PROJECT_ROOT / filename
        target.write_text(content)
        return f"已產生 {target}，內容：\n\n{content}"

    def codegen(self, url: str, output: str = "recorded_test.py") -> str:
        target = PROJECT_ROOT / output
        subprocess.run(
            ["playwright", "codegen", "-o", str(target), url],
            cwd=PROJECT_ROOT,
        )
        return f"錄製完成，已存至 {target}"
