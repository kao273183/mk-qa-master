import json
from .base import TestRunner
from ..config import PROJECT_ROOT, REPORT_PATH
from ..security import safe_run


CYPRESS_TEMPLATE = '''/**
 * {description}
 */
describe("{slug}", () => {{
  it("TODO: 由 Claude 補完", () => {{
    cy.visit("/");
  }});
}});
'''


class CypressRunner(TestRunner):
    name = "cypress"

    def list_tests(self) -> str:
        e2e_dir = PROJECT_ROOT / "cypress" / "e2e"
        if not e2e_dir.exists():
            return "（找不到 cypress/e2e 目錄）"
        files = [str(p.relative_to(PROJECT_ROOT)) for p in e2e_dir.rglob("*.cy.*")]
        return "\n".join(files) or "（沒有測試檔）"

    def run_tests(self, filter=None, **kwargs) -> dict:
        cmd = [
            "npx", "cypress", "run",
            "--reporter", "json",
            "--reporter-options", f"output={REPORT_PATH}",
        ]
        if filter:
            cmd.extend(["--spec", f"**/*{filter}*"])
        result = safe_run(cmd, cwd=PROJECT_ROOT)
        return {
            "exit_code": result.returncode,
            "stdout_tail": result.stdout[-2000:],
            "stderr_tail": result.stderr[-1000:],
        }

    def run_failed(self) -> dict:
        # Cypress 沒有原生 --lastFailed；解析上次報告找失敗 spec 重跑
        if not REPORT_PATH.exists():
            return self.run_tests()
        data = json.loads(REPORT_PATH.read_text())
        failed_files = {
            t.get("file") for t in data.get("failures", []) if t.get("file")
        }
        if not failed_files:
            return {"exit_code": 0, "stdout_tail": "（沒有失敗）"}
        cmd = [
            "npx", "cypress", "run",
            "--reporter", "json",
            "--reporter-options", f"output={REPORT_PATH}",
            "--spec", ",".join(failed_files),
        ]
        result = safe_run(cmd, cwd=PROJECT_ROOT)
        return {"exit_code": result.returncode, "stdout_tail": result.stdout[-2000:]}

    def get_report_summary(self) -> dict:
        if not REPORT_PATH.exists():
            return {"error": "找不到報告"}
        data = json.loads(REPORT_PATH.read_text())
        stats = data.get("stats", {})
        return {
            "total": stats.get("tests", 0),
            "passed": stats.get("passes", 0),
            "failed": stats.get("failures", 0),
            "skipped": stats.get("pending", 0),
            "duration": stats.get("duration"),
        }

    def get_failure_details(self, test_id=None) -> list[dict]:
        if not REPORT_PATH.exists():
            return [{"error": "找不到報告"}]
        data = json.loads(REPORT_PATH.read_text())
        failures = data.get("failures", [])
        if test_id:
            failures = [t for t in failures if test_id in t.get("fullTitle", "")]
        return [
            {
                "nodeid": t.get("fullTitle"),
                "message": t.get("err", {}).get("stack", ""),
                "duration": t.get("duration"),
            }
            for t in failures
        ]

    def generate_test(self, description: str, filename: str) -> str:
        if not filename.endswith(".cy.js"):
            filename = f"{filename}.cy.js"
        slug = filename.replace(".cy.js", "")
        content = CYPRESS_TEMPLATE.format(description=description, slug=slug)
        target = PROJECT_ROOT / "cypress" / "e2e" / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return f"已產生 {target}，內容：\n\n{content}"
