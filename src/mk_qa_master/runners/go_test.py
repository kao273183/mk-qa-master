import json
from .base import TestRunner
from ..config import PROJECT_ROOT, REPORT_PATH
from ..security import safe_run


GO_TEMPLATE = '''package main

import "testing"

// {description}
func Test{slug}(t *testing.T) {{
\t// TODO: 由 Claude 補完
\tt.Log("not implemented")
}}
'''


class GoTestRunner(TestRunner):
    name = "go"

    def list_tests(self) -> str:
        result = safe_run(["go", "test", "-list", ".*", "./..."], cwd=PROJECT_ROOT)
        return result.stdout or result.stderr

    def run_tests(self, filter=None, **kwargs) -> dict:
        cmd = ["go", "test", "-json", "./..."]
        if filter:
            cmd.extend(["-run", filter])
        result = safe_run(cmd, cwd=PROJECT_ROOT)
        REPORT_PATH.write_text(result.stdout)
        return {
            "exit_code": result.returncode,
            "stdout_tail": result.stdout[-2000:],
            "stderr_tail": result.stderr[-1000:],
        }

    def run_failed(self) -> dict:
        if not REPORT_PATH.exists():
            return self.run_tests()
        failed: set[str] = set()
        for line in REPORT_PATH.read_text().splitlines():
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            if evt.get("Action") == "fail" and evt.get("Test"):
                failed.add(evt["Test"])
        if not failed:
            return {"exit_code": 0, "stdout_tail": "（沒有失敗）"}
        pattern = "^(" + "|".join(failed) + ")$"
        return self.run_tests(filter=pattern)

    def get_report_summary(self) -> dict:
        if not REPORT_PATH.exists():
            return {"error": "找不到報告"}
        passed = failed = skipped = 0
        for line in REPORT_PATH.read_text().splitlines():
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not evt.get("Test"):
                continue
            action = evt.get("Action")
            if action == "pass":
                passed += 1
            elif action == "fail":
                failed += 1
            elif action == "skip":
                skipped += 1
        return {
            "total": passed + failed + skipped,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
        }

    def get_failure_details(self, test_id=None) -> list[dict]:
        if not REPORT_PATH.exists():
            return [{"error": "找不到報告"}]
        outputs: dict[str, list[str]] = {}
        failed: set[str] = set()
        for line in REPORT_PATH.read_text().splitlines():
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            test = evt.get("Test")
            if not test:
                continue
            key = f"{evt.get('Package')}::{test}"
            if evt.get("Action") == "output":
                outputs.setdefault(key, []).append(evt.get("Output", ""))
            elif evt.get("Action") == "fail":
                failed.add(key)
        results = []
        for key in failed:
            if test_id and test_id not in key:
                continue
            results.append({"nodeid": key, "message": "".join(outputs.get(key, []))})
        return results

    def generate_test(self, description: str, filename: str) -> str:
        if not filename.endswith("_test.go"):
            filename = f"{filename}_test.go"
        slug = filename.replace("_test.go", "").title().replace("_", "")
        content = GO_TEMPLATE.format(description=description, slug=slug)
        target = PROJECT_ROOT / filename
        target.write_text(content)
        return f"已產生 {target}，內容：\n\n{content}"
