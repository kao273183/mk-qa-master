import json
from .base import TestRunner
from ..config import PROJECT_ROOT, REPORT_PATH
from ..security import safe_run


JEST_TEMPLATE = '''/**
 * {description}
 */
describe("{slug}", () => {{
  test("TODO: 由 Claude 補完", () => {{
    expect(true).toBe(true);
  }});
}});
'''


class JestRunner(TestRunner):
    name = "jest"

    def list_tests(self) -> str:
        result = safe_run(["npx", "jest", "--listTests"], cwd=PROJECT_ROOT)
        return result.stdout or result.stderr

    def run_tests(self, filter=None, **kwargs) -> dict:
        cmd = ["npx", "jest", "--json", f"--outputFile={REPORT_PATH}"]
        if filter:
            cmd.extend(["-t", filter])
        result = safe_run(cmd, cwd=PROJECT_ROOT)
        return {
            "exit_code": result.returncode,
            "stdout_tail": result.stdout[-2000:],
            "stderr_tail": result.stderr[-1000:],
        }

    def run_failed(self) -> dict:
        cmd = ["npx", "jest", "--onlyFailures", "--json", f"--outputFile={REPORT_PATH}"]
        result = safe_run(cmd, cwd=PROJECT_ROOT)
        return {"exit_code": result.returncode, "stdout_tail": result.stdout[-2000:]}

    def get_report_summary(self) -> dict:
        if not REPORT_PATH.exists():
            return {"error": "找不到報告，請先執行 run_tests"}
        data = json.loads(REPORT_PATH.read_text())
        return {
            "total": data.get("numTotalTests", 0),
            "passed": data.get("numPassedTests", 0),
            "failed": data.get("numFailedTests", 0),
            "skipped": data.get("numPendingTests", 0),
            "duration": data.get("startTime"),
        }

    def get_failure_details(self, test_id=None) -> list[dict]:
        if not REPORT_PATH.exists():
            return [{"error": "找不到報告"}]
        data = json.loads(REPORT_PATH.read_text())
        results = []
        for suite in data.get("testResults", []):
            for t in suite.get("assertionResults", []):
                if t.get("status") != "failed":
                    continue
                nodeid = f"{suite.get('name')}::{t.get('fullName')}"
                if test_id and test_id not in nodeid:
                    continue
                results.append({
                    "nodeid": nodeid,
                    "message": "\n".join(t.get("failureMessages", [])),
                    "duration": t.get("duration"),
                })
        return results

    def generate_test(self, description: str, filename: str) -> str:
        if not (filename.endswith(".test.js") or filename.endswith(".spec.js")):
            filename = f"{filename}.test.js"
        slug = filename.replace(".test.js", "").replace(".spec.js", "")
        content = JEST_TEMPLATE.format(description=description, slug=slug)
        target = PROJECT_ROOT / filename
        target.write_text(content)
        return f"已產生 {target}，內容：\n\n{content}"
