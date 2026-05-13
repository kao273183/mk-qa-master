import ast
import importlib.util
import json
import re
import shutil
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path
from .base import TestRunner
from ..config import PROJECT_ROOT, REPORT_PATH, JUNIT_PATH, ARTIFACTS_DIR, HISTORY_DIR


def _parse_docstrings(file_path: Path) -> dict[str, str]:
    """Read a test .py file and return {func_name: docstring} for every
    function whose docstring is present (parsed via ast — no import / side
    effects). Returns {} on missing file or syntax error.
    """
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return {}
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node)
            if doc:
                out[node.name] = doc.strip()
    return out


# Trace events whose class.method we want to surface as user-visible "steps".
# Real trace.trace (Playwright 1.59+) uses `class` + `method` fields with
# Capitalized class names — not the older `apiName` string. Internal events
# (tracing.*, etc.) get filtered out by simply not matching this pattern.
_STEP_KEEP_PATTERN = re.compile(r"^(Frame|Page|Locator|ElementHandle|BrowserContext|Keyboard|Mouse)\.")


# Detect once at module load — pytest-rerunfailures lets us auto-retry transient
# failures so the optimizer's flake signal is grounded in repeat-confirmed fails.
_HAS_RERUNFAILURES = importlib.util.find_spec("pytest_rerunfailures") is not None


# Optional layout-integrity check — appended to generated tests as a
# commented hint. Catches 跑版（text overflow / hard-px width / container
# escape）at the current viewport. Commented because sites with
# intentional horizontal scrollers would always fail it; user opts in.
_OVERFLOW_HINT = (
    "    # Optional layout sanity (uncomment to catch 跑版: text/element 溢出 container):\n"
    "    # overflow = page.evaluate(\"\"\"() => [...document.querySelectorAll('body *')]\n"
    "    #   .filter(e => {\n"
    "    #     const cs = getComputedStyle(e);\n"
    "    #     if (['auto','scroll'].includes(cs.overflowX) || ['auto','scroll'].includes(cs.overflowY)) return false;\n"
    "    #     return e.scrollWidth > e.clientWidth + 2 || e.scrollHeight > e.clientHeight + 2;\n"
    "    #   }).length\"\"\")\n"
    "    # expect(overflow).to_equal(0)\n"
)


TEST_TEMPLATE = '''from playwright.sync_api import Page, expect


def test_{slug}(page: Page):
    """{description}"""
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
            # always-on: reporter surfaces pass-state screenshots + step lists,
            # not just failures. Video stays retain-on-failure (heavy + only
            # useful for debugging breaks); tracing has to be on for step
            # extraction even on passes.
            "--screenshot=on",
            "--video=retain-on-failure",
            "--tracing=on",
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
        # \w preserves underscores; playwright-pytest sanitizes those to dashes
        # too. Excluding underscores from the kept set keeps our token aligned
        # with the on-disk folder name (e.g. test_a_b → test-a-b).
        token = re.sub(r"[^a-z0-9]+", "-", test_func.lower()).strip("-")
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

    def get_all_test_details(self) -> list[dict]:
        """Per-test details (outcome / duration / artifacts / steps / title).

        `title` comes from the test function's docstring — that's the most
        readable "case name" we have. Falls back to None when no docstring is
        present, and the reporter then shows the nodeid alone.

        The reporter uses this to render pass + fail sections in one pass.
        `rerun` records are skipped — pytest-rerunfailures emits them as a
        pre-marker before the final outcome and we don't want them shown as
        their own test.
        """
        if not REPORT_PATH.exists():
            return []
        try:
            data = json.loads(REPORT_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            return []
        # Cache docstring extraction per file: typical suite has many tests in
        # the same .py, so re-parsing each time would scale poorly.
        docstring_cache: dict[Path, dict[str, str]] = {}
        results: list[dict] = []
        for t in data.get("tests", []) or []:
            nodeid = t.get("nodeid")
            outcome = t.get("outcome")
            if not nodeid or outcome == "rerun":
                continue
            artifacts = self._find_artifacts(nodeid)
            results.append({
                "nodeid": nodeid,
                "title": self._docstring_for(nodeid, docstring_cache),
                "outcome": outcome,
                "duration": (t.get("call") or {}).get("duration"),
                "message": (t.get("call") or {}).get("longrepr", "") if outcome == "failed" else "",
                "steps": self._extract_steps(artifacts.get("trace")),
                **artifacts,
            })
        return results

    def _docstring_for(self, nodeid: str, cache: dict[Path, dict[str, str]]) -> str | None:
        """Look up the test function's docstring as the human-readable case name.

        nodeid forms handled:
          - tests/x.py::test_y
          - tests/x.py::TestSuite::test_y       (class-based)
          - tests/x.py::test_y[param-id]        (parametrize)
        We walk all FunctionDef/AsyncFunctionDef in the file (cheap, cached)
        and key by bare function name. Parametrize IDs and class scoping
        share the same source function, so collapsing both to the function
        name is the right move.
        """
        file_part, _, suffix = nodeid.partition("::")
        if not file_part or not suffix:
            return None
        func_name = suffix.split("::")[-1].split("[")[0]
        file_path = PROJECT_ROOT / file_part
        if file_path not in cache:
            cache[file_path] = _parse_docstrings(file_path)
        return cache[file_path].get(func_name)

    def _extract_steps(self, trace_zip_path: str | None) -> list[dict]:
        """Parse trace.zip → list of {api, title} user-facing actions.

        Real trace.trace (Playwright 1.59+) emits one `before` event per call
        with `class` + `method` fields (no more `apiName`). We only take
        `before` events — `after` events repeat the callId with timing/error
        data we don't currently surface. callId dedup is no longer strictly
        needed but kept as a safety net for older trace formats.
        """
        if not trace_zip_path:
            return []
        p = Path(trace_zip_path)
        if not p.is_file():
            return []
        seen_ids: set[str] = set()
        steps: list[dict] = []
        try:
            with zipfile.ZipFile(p) as z:
                target = next(
                    (n for n in z.namelist() if n.endswith("trace.trace")),
                    None,
                )
                if not target:
                    return []
                with z.open(target) as f:
                    for raw in f:
                        try:
                            ev = json.loads(raw.decode("utf-8", "replace"))
                        except json.JSONDecodeError:
                            continue
                        if ev.get("type") != "before":
                            continue
                        cls = ev.get("class") or ""
                        method = ev.get("method") or ""
                        if not cls or not method:
                            continue
                        api = f"{cls}.{method}"
                        if not _STEP_KEEP_PATTERN.match(api):
                            continue
                        call_id = ev.get("callId") or ""
                        if call_id and call_id in seen_ids:
                            continue
                        if call_id:
                            seen_ids.add(call_id)
                        steps.append({
                            "api": api,
                            "title": ev.get("title") or api,
                        })
        except (zipfile.BadZipFile, OSError):
            return []
        return steps

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
        business_context: str | None = None,
    ) -> str:
        if not filename.startswith("test_"):
            filename = f"test_{filename}"
        if not filename.endswith(".py"):
            filename += ".py"
        slug = filename.replace("test_", "").replace(".py", "")

        # If caller hands us a module from analyze_url, render a runnable
        # skeleton with concrete selectors instead of a `# TODO` stub.
        if isinstance(module, dict) and module.get("kind") == "form":
            content = self._render_form_test(description, slug, url, module, business_context)
        elif isinstance(module, dict):
            content = self._render_generic_module_test(description, slug, url, module, business_context)
        else:
            content = self._render_basic_test(description, slug, business_context)

        target = PROJECT_ROOT / filename
        target.write_text(content)
        return f"已產生 {target}，內容：\n\n{content}"

    def _business_context_block(self, business_context: str | None) -> str:
        """Indented `# Business context:` comment block for inside a test fn.

        Why a comment vs a docstring: the docstring slot is already the case
        name (single-line summary). Business context is supplementary
        detail — putting it as comments keeps it visible in the source but
        out of the way of automated docstring tooling / report headers.
        """
        if not business_context or not str(business_context).strip():
            return ""
        lines = ["    # Business context:"]
        for raw in str(business_context).strip().splitlines():
            stripped = raw.rstrip()
            lines.append(f"    # {stripped}" if stripped else "    #")
        return "\n".join(lines) + "\n"

    def _render_basic_test(self, description: str, slug: str, business_context: str | None) -> str:
        bc = self._business_context_block(business_context)
        return (
            "from playwright.sync_api import Page, expect\n\n\n"
            f"def test_{slug}(page: Page):\n"
            f"    {description!r}\n"
            f"{bc}"
            "    # TODO: 由 Claude 補完實作\n"
            '    page.goto("https://example.com")\n'
            '    expect(page).to_have_title("Example Domain")\n'
            f"{_OVERFLOW_HINT}"
        )

    def _render_form_test(self, description: str, slug: str, url: str | None, module: dict, business_context: str | None = None) -> str:
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
        # description goes on the *function* docstring so the HTML reporter
        # picks it up as the case name. Module docstring keeps just the
        # auto-gen trace for grep-ability.
        bc = self._business_context_block(business_context)
        return (
            f'"""Auto-generated from analyze_url module: {module.get("name", "(unnamed)")} (kind=form)"""\n'
            "from playwright.sync_api import Page, expect\n\n\n"
            f"def test_{slug}(page: Page):\n"
            f"    {description!r}\n"
            f"{bc}"
            f"    page.goto({goto_url!r})\n"
            f"{fills_body}\n"
            f"{submit_body}\n"
            + (f"{tc_block}\n" if tc_block else "")
            + "    # TODO: 補上實際斷言，例如：\n"
              "    # expect(page).to_have_url(...)\n"
              '    # expect(page.get_by_text("成功")).to_be_visible()\n'
            + _OVERFLOW_HINT
        )

    def _render_generic_module_test(self, description: str, slug: str, url: str | None, module: dict, business_context: str | None = None) -> str:
        kind = module.get("kind", "unknown")
        sel = module.get("selectors") or {}
        target_sel = sel.get("container") or sel.get("trigger") or "body"
        tcs = module.get("candidate_tcs") or []
        tc_block = "\n".join(f"    # TC: {tc}" for tc in tcs[:3])
        goto_url = url or "https://example.com"
        bc = self._business_context_block(business_context)
        return (
            f'"""Auto-generated from analyze_url module: {module.get("name", "(unnamed)")} (kind={kind})"""\n'
            "from playwright.sync_api import Page, expect\n\n\n"
            f"def test_{slug}(page: Page):\n"
            f"    {description!r}\n"
            f"{bc}"
            f"    page.goto({goto_url!r})\n"
            f"    target = page.locator({target_sel!r})\n"
            "    expect(target).to_be_visible()\n"
            + (f"{tc_block}\n" if tc_block else "")
            + "    # TODO: 補上實際互動與斷言\n"
            + _OVERFLOW_HINT
        )

    def codegen(self, url: str, output: str = "recorded_test.py") -> str:
        target = PROJECT_ROOT / output
        subprocess.run(
            ["playwright", "codegen", "-o", str(target), url],
            cwd=PROJECT_ROOT,
        )
        return f"錄製完成，已存至 {target}"
