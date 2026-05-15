import asyncio
import json
import time
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, Resource
from pydantic import AnyUrl

from .tools import runner, reporter, generator, analyzer, telemetry, optimizer, qa_context
from .runners import get_runner, REGISTRY
from .reporters import html as html_reporter
from .config import REPORT_PATH, OPTIMIZATION_PATH

app = Server("mcp-test-runner")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="get_runner_info",
            description=(
                "回傳目前由 QA_RUNNER 環境變數選定的測試 runner（pytest / jest / cypress / "
                "go / maestro 五選一）加上 server 編譯時內建的全部 runner 清單。"
                "建議每個 session 第一個呼叫——AI 用它判斷後續該產 Playwright .py 還是 "
                "Maestro .yaml、要不要 headed browser，避免後面拿錯模板。"
                "也用來確認專案環境設定正確：QA_PROJECT_ROOT 指對地方、QA_RUNNER 沒拼錯。"
                "回傳 shape：{active: 'pytest', available: ['pytest', 'jest', ...]}。"
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="list_tests",
            description=(
                "用 runner 的原生 collection 機制列出受測專案內所有可執行測試："
                "pytest 走 `pytest --collect-only`、Jest 走 `npx jest --listTests`、"
                "Cypress 走 `cypress/e2e/*.cy.*` glob、Go 走 `go test -list .*`、"
                "Maestro 走 `*.yaml` 遞迴掃。回傳一份逐行 nodeid / 檔名清單。"
                "用法：run_tests 前確認 collection 沒漏、generate_test 前避免跟既有 case 重複。"
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="run_tests",
            description=(
                "Execute the test suite under the active QA_RUNNER and produce a structured "
                "report. The single most-called tool — invoke whenever a user says "
                "「跑/run/test/check/驗證/執行」, after generate_test (verify new test), "
                "or after a fix (confirm bug gone).\n\n"
                "Behavior:\n"
                "- Invokes the runner's native CLI under QA_PROJECT_ROOT — pytest with "
                "  --screenshot=on / --tracing=on / --video=retain-on-failure, or "
                "  `npx jest --json`, `npx cypress run --reporter json`, `go test -json`, "
                "  `maestro test --format junit`\n"
                "- Optional `filter` narrows the scope: pytest -k expr, jest -t pattern, "
                "  cypress --spec glob, go -run regex, maestro flow-name substring\n"
                "- Writes report.json (pytest-json-report shape, runner-agnostic) + JUnit XML\n"
                "- Snapshots the run into history/ and auto-triggers optimizer.write_plan() "
                "  → optimization-plan.md is refreshed\n"
                "- Maestro: auto-retries flows that failed on first attempt (MAESTRO_RETRY=true), "
                "  surfaces flaky_in_run count\n"
                "Returns: {exit_code, raw_exit_code, stdout_tail, stderr_tail, retry_enabled, "
                "flaky_in_run, ...}\n\n"
                "When to use:\n"
                "- After writing a new test → verify it actually passes\n"
                "- Smoke before a release\n"
                "- Whenever the user prompt contains a run/test verb\n\n"
                "When NOT to use:\n"
                "- Inspecting last results without re-running → use get_test_report (cheaper)\n"
                "- Re-running only failed cases → use run_failed (way faster)\n"
                "- Enumerating which tests exist → use list_tests\n\n"
                "Edge cases:\n"
                "- No tests match `filter` → exit_code != 0 with 「no tests ran」 in stderr_tail\n"
                "- QA_TIMEOUT_SECONDS exceeded → exit_code 124 + `[TIMEOUT…]` tag in stderr_tail\n"
                "- `filter` starting with `-` or containing `..` → blocked by security "
                "  guardrail, returns {error: …}"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "filter": {
                        "type": "string",
                        "description": (
                            "選填，測試名稱關鍵字。pytest 走 -k 表達式（支援 and/or/not）、"
                            "Jest 走 -t、Cypress 走 --spec '**/*<filter>*'、Go 走 -run "
                            "regex、Maestro 在 flow 檔名作子字串比對。"
                        ),
                    },
                    "headed": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "選填，僅對 pytest-playwright 有效。True 時瀏覽器有 UI 模式跑（適合 debug、"
                            "看 flake 視覺現象）；預設 headless 跑、CI / 大量套件用這個。"
                        ),
                    },
                    "browser": {
                        "type": "string",
                        "enum": ["chromium", "firefox", "webkit"],
                        "default": "chromium",
                        "description": (
                            "選填，僅對 pytest-playwright 有效，指定 Playwright 啟用的 browser engine。"
                            "需事先 `playwright install <browser>` 過。"
                        ),
                    },
                },
            },
        ),
        Tool(
            name="run_failed",
            description=(
                "只重跑上次失敗的測試——比跑整套套件快很多，適合修完一個 bug 後驗證迭代。"
                "pytest 走 `--lf`（last-failed）、Jest 走 `--onlyFailures`、"
                "Cypress 解析上次 report.json 的 failures[] 反查 spec 重跑、"
                "Go 撈失敗的 Test 名組成 regex 餵 -run、Maestro 反查 nodeid 對應 .yaml 重跑。"
                "需要先有過一次 run_tests（不然 report.json 不存在）。"
                "回傳 shape 跟 run_tests 一樣，接 get_test_report / get_failure_details 同樣方式檢視。"
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_test_report",
            description=(
                "讀上一次 run_tests 留下的 report.json，回傳一個輕量摘要："
                "total / passed / failed / skipped / flaky_in_run（auto-retry 救回的數量）/ "
                "duration（秒）。比再跑一次 suite 便宜得多——適合在連續操作中間反覆查狀態。"
                "未跑過時回 {error: 找不到報告，請先執行 run_tests}。"
                "拿到摘要後若 failed > 0，接 get_failure_details 拿錯誤細節。"
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_failure_details",
            description=(
                "Extract full root-cause-analysis materials for every failed test in the "
                "most recent run.\n\n"
                "Behavior:\n"
                "- Reads report.json, filters tests where outcome == 「failed」\n"
                "- pytest: parses Playwright trace.zip → extracts real API call sequence "
                "  (Frame.*, Page.*, Locator.*, ElementHandle.* events) as steps[]\n"
                "- Maestro: parses flow YAML for `takeScreenshot:` directives → resolves "
                "  <name>.png at PROJECT_ROOT root\n"
                "- Best-effort resolves screenshot / trace.zip / video / recording paths "
                "  from --output / --debug-output artifact directories\n"
                "Returns: list[{nodeid, title, message, duration, steps[], screenshot, "
                "trace, video}]\n\n"
                "When to use:\n"
                "- run_tests just reported failed > 0 → drill into each case\n"
                "- User asks 「why did it fail / show me the trace / what broke」\n"
                "- Filing a JIRA bug → use the artifact paths to attach screenshot+trace\n"
                "- Comparing failure signatures across runs (pair with get_test_history)\n\n"
                "When NOT to use:\n"
                "- Want the summary count only → use get_test_report (lighter)\n"
                "- No tests have been run yet → returns [{error: 「找不到報告」}]\n"
                "- Want details for PASSING tests too → not supported here; the HTML "
                "  reporter renders those via a different path\n\n"
                "Edge cases:\n"
                "- test_id substring matches nothing → empty list, no error\n"
                "- screenshot/trace/video missing on disk → those fields are null but "
                "  the entry stays\n"
                "- Retry-recovered flake (was failed, now passed) → not listed here; "
                "  surfaces in summary.flaky_in_run instead"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "test_id": {
                        "type": "string",
                        "description": (
                            "選填，僅回傳 nodeid 含此關鍵字的 case（substring match，不分大小寫）。"
                            "省略則回傳全部失敗 case。常用模式：先全部抓→看到特定模式後再用 test_id 收斂。"
                        ),
                    }
                },
            },
        ),
        Tool(
            name="generate_test",
            description=(
                "產生 pytest-playwright 測試骨架。"
                "推薦流程：先呼叫 analyze_url 拿 candidate_tcs，"
                "再對每條想覆蓋的 TC 呼叫一次 generate_test、把該 candidate_tc 整段字串當 description 傳入"
                " — 這段會自動寫成 test 函式的 docstring，HTML 報告會把它當作 case 名稱顯示。"
                "若提供 url+module（來自 analyze_url 的 modules[]），會用 selectors 預填可執行版本。"
                "若想一次處理整個 URL、不想自己編排，請改用 auto_generate_tests。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": (
                            "test 的描述文字。會直接寫成產出 test 函式的 docstring（pytest）"
                            "或 YAML 開頭註解（Maestro），HTML 報告會用這段當 case 名稱顯示。"
                            "建議直接傳 analyze_url / analyze_screen 回來的某個 candidate_tc 整段字串。"
                        ),
                    },
                    "filename": {
                        "type": "string",
                        "description": (
                            "輸出檔名，相對於 PROJECT_ROOT。pytest 用 .py、Maestro 用 .yaml、"
                            "Jest 用 .test.js、Cypress 用 .cy.js、Go 用 _test.go。"
                            "不可絕對路徑、不可含 `..`（會被 security guardrail 擋）。"
                        ),
                    },
                    "url": {
                        "type": "string",
                        "description": "選填，受測 URL；提供後 page.goto 會預填",
                    },
                    "module": {
                        "type": "object",
                        "description": "選填，analyze_url 結果 modules[] 中的一個項目；提供後會用 selectors 預填",
                    },
                    "business_context": {
                        "type": "string",
                        "description": (
                            "選填，業務規則 / 歷史 Bug / 標準斷言文字 等領域知識。"
                            "提供後會以 `# Business context:` 註解區塊印進 test 函式內，"
                            "讓人類 reviewer 與後續 AI 都能看到設計依據。"
                            "建議先 call get_qa_context() 拿到相關 section 再傳入。"
                        ),
                    },
                },
                "required": ["description", "filename"],
            },
        ),
        Tool(
            name="codegen",
            description=(
                "Launch interactive test recording for the active runner. Useful as a "
                "baseline-builder before refining with generate_test.\n\n"
                "Behavior:\n"
                "- pytest-playwright: spawns `playwright codegen -o <output> <url>` — a real "
                "  Chromium window opens, you click / type / navigate, Playwright transcribes "
                "  every action into runnable pytest code, output is saved to "
                "  PROJECT_ROOT/<output> on browser close\n"
                "- Maestro: returns a human-readable hint string pointing at "
                "  `maestro studio` (no shell-able codegen exists for it)\n"
                "- jest / cypress / go runners: same Maestro-style fallback hint\n"
                "Returns: a string with the saved path or the manual-record hint.\n\n"
                "When to use:\n"
                "- Building a baseline happy-path test interactively (you click, it transcribes)\n"
                "- Site has complex auth / JS state you'd rather not script by hand\n"
                "- Quick prototype before refining with generate_test\n"
                "- User says 「record / 錄製 / use codegen / 紀錄操作」\n\n"
                "When NOT to use:\n"
                "- Headless CI / container environments → can't open Chromium\n"
                "- Need structured, AI-driven test generation from analysis → use "
                "  generate_test or auto_generate_tests instead\n"
                "- One-shot per-module test coverage → use auto_generate_tests\n"
                "- Mobile UI flows → returns a hint anyway, consider analyze_screen + "
                "  generate_test instead\n\n"
                "Edge cases:\n"
                "- `output` contains `..` or is absolute → blocked by security guardrail\n"
                "- Chromium not installed → playwright codegen fails; user sees the "
                "  `playwright install` hint in stderr"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": (
                            "受測 URL。Playwright codegen 會開瀏覽器 navigate 到此網址、"
                            "從這頁開始錄製你的互動。"
                        ),
                    },
                    "output": {
                        "type": "string",
                        "default": "recorded_test.py",
                        "description": (
                            "選填，輸出檔名（相對於 PROJECT_ROOT，不可絕對路徑、不可含 `..`）。"
                            "預設 `recorded_test.py`。"
                        ),
                    },
                },
                "required": ["url"],
            },
        ),
        Tool(
            name="generate_html_report",
            description=(
                "把最近一次 run_tests 的結果渲染成單檔自包含 HTML——base64 內嵌截圖、"
                "嵌入式 step list、history sparkline 走勢、折疊的 Passed 區塊、展開的 Failed cards。"
                "沒外部 CSS/JS 依賴，可以直接寄信、丟靜態 host、貼到 Slack。"
                "預設輸出 PROJECT_ROOT/report.html。實作位於 reporters/html.py，"
                "走 sample_report.html 同款設計。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "output": {
                        "type": "string",
                        "default": "report.html",
                        "description": (
                            "選填，輸出檔名（相對於 QA_PROJECT_ROOT）。"
                            "預設 `report.html`。"
                        ),
                    },
                },
            },
        ),
        Tool(
            name="get_test_history",
            description=(
                "遍歷 test-results/history/*.json 快照（每次 run_tests 完會自動歸檔），"
                "回傳逐次摘要：timestamp / total / passed / failed / skipped / "
                "duration / pass_rate(0-100)。用於 flake 分析（『這條測試上週一直 fail 嗎』）、"
                "速度退化分析（『duration 是不是越來越長』）、覆蓋趨勢圖。"
                "預設回最近 10 次，limit 可調 1-100。"
                "想要可執行行動建議的話接 get_optimization_plan，它已綜合 history + telemetry。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "default": 10,
                        "minimum": 1,
                        "maximum": 100,
                        "description": (
                            "選填，回最近 N 次 run 的摘要。"
                            "1-100，預設 10。長期 flake 分析建議 30+。"
                        ),
                    },
                },
            },
        ),
        Tool(
            name="get_optimization_plan",
            description=(
                "綜合 history/ 快照、telemetry tool-usage、analyze_url 偵測過的 modules，"
                "產出三層自我強化分析："
                "(1) 測試套件品質：每條 test 算 outcomes 字串（PFPFP 那種）→ flake_score、"
                "再對失敗 error signature 做指紋比對，連 3 次相同 signature 升級為 broken，"
                "duration 退化超 1.5x 標記 slow_regression，否則 stable_passing；"
                "(2) MCP 使用模式：top tool、重複 args、錯誤率、常見呼叫鏈（A→B 共現）；"
                "(3) AI 產測效益：generate_test 寫的 test 有沒有出現在下一次 run、"
                "analyze_url 偵測到的 module 對不對得到 test 檔（採用率 vs 覆蓋缺口）。"
                "回傳結構化 JSON 並同步寫進 PROJECT_ROOT/optimization-plan.md。"
                "每次 run_tests 結束會自動 trigger 一次、所以這個 tool 用來「即時讀」結果。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "history_limit": {
                        "type": "integer",
                        "default": 10,
                        "minimum": 1,
                        "maximum": 100,
                        "description": (
                            "選填，套件品質分析會看最近 N 次 history 快照。"
                            "1-100，預設 10。flake score 至少要 5 次以上才穩，"
                            "深度分析建議 30+。"
                        ),
                    },
                    "telemetry_limit": {
                        "type": "integer",
                        "default": 500,
                        "minimum": 10,
                        "maximum": 5000,
                        "description": (
                            "選填，MCP 使用模式分析會看 telemetry 最近 N 筆 tool-call。"
                            "10-5000，預設 500。長期使用模式分析拉到 2000+，"
                            "近期問題排查 100-200 就夠。"
                        ),
                    },
                },
            },
        ),
        Tool(
            name="analyze_url",
            description=(
                "Probe a live web page in headless Chromium and return a structured map of "
                "testable modules plus the API endpoints the page actually called. The web "
                "counterpart of analyze_screen.\n\n"
                "Behavior:\n"
                "- page.goto(url) with DOMContentLoaded + 5s networkidle wait\n"
                "- DOM probe extracts five module kinds: form (with fields[] + required "
                "  flags), nav (link lists), dialog (modal containers), section (labeled "
                "  regions), cta (action buttons matching action keywords like 登入/送出/"
                "  Login/Submit)\n"
                "- Each module gets a candidate_tcs[] — domain-aware test case strings "
                "  ready to paste into generate_test\n"
                "- Records every fetch/XHR the page issues, dedupes by (method, path), "
                "  adds endpoint-specific candidate TCs (401, 404, 4xx, payload-too-large…)\n"
                "- Layout overflow scan flags visible elements whose content escapes its "
                "  container by >2 px horizontal / >10 px vertical (跑版 / text-overflow)\n"
                "Returns: {url, page_title, scanned_at, modules[], api_endpoints[], "
                "layout_warnings[]}\n\n"
                "When to use:\n"
                "- User wants tests for a specific URL or page\n"
                "- Designing regression coverage from real user-facing behavior\n"
                "- Need backend API coverage hints (api_endpoints[] gives methods + paths)\n"
                "- Investigating layout bugs at the current viewport\n"
                "- Pair with generate_test(module=…) for one runnable test per module\n\n"
                "When NOT to use:\n"
                "- Mobile apps (no DOM) → use analyze_screen\n"
                "- Want analysis + immediate test generation → use auto_generate_tests "
                "  (one-shot version)\n"
                "- Looking for existing tests → use list_tests\n"
                "- Single-page testing prototype → use codegen instead\n\n"
                "Edge cases:\n"
                "- URL unreachable / timeout → returns {error: 「打開頁面失敗…」, url}\n"
                "- Page has 0 forms / 0 ctas → modules[] is empty but the call succeeds\n"
                "- Login-walled URL with no auth_cookie → analyzes the login page (less "
                "  useful) — pass auth_cookie to reach post-login pages\n"
                "- SPA with delayed hydration → bump timeout_ms to 30000+"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "要分析的網頁 URL，需含 protocol（http:// 或 https://）。",
                    },
                    "timeout_ms": {
                        "type": "integer",
                        "default": 15000,
                        "description": (
                            "選填，page.goto 等待 DOMContentLoaded 的逾時毫秒數。"
                            "之後額外 wait 5 秒讓 networkidle（XHR 載入）穩定。"
                            "預設 15000。慢站 / 需要 SSR / 重 JS hydration 的網站可拉到 30000+。"
                        ),
                    },
                    "auth_cookie": {
                        "type": "string",
                        "description": (
                            "選填，預先注入登入 cookie，格式：`name1=value1; name2=value2`（一行 cookie header）。"
                            "用法：先在瀏覽器 DevTools / Application / Cookies 複製值再貼進來。"
                            "用於分析需要登入後才看得到的頁面。"
                        ),
                    },
                },
                "required": ["url"],
            },
        ),
        Tool(
            name="analyze_screen",
            description=(
                "Mobile 版的 analyze_url：透過 `maestro hierarchy` dump 當前 iOS Simulator / "
                "Android Emulator / 實體機 / BlueStacks（透過 QA_ANDROID_HOST）前景 app 的 view tree，"
                "再分類成 form（具 hint_text 的輸入欄位）、cta（enabled + 有文字的可點元件）、"
                "tab_bar（selected 狀態 + 同 y 對齊的 2+ 個 tab）三種 modules 並附 candidate_tcs。"
                "內建 noise filter 自動排除 iOS 狀態列 + asset 命名標籤（bg_* / *_filled / 純數字 / "
                "單一 ASCII 字元等）讓結果信號集中。需 Maestro CLI 已裝、裝置 booted、app 已在前景。"
                "若給 app_id + launch_app=true，會先用 launchApp 啟動再 dump。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "app_id": {
                        "type": "string",
                        "description": (
                            "選填，bundle id (iOS) / package name (Android)，"
                            "格式如 `com.example.app`。搭配 launch_app=true 使用，"
                            "或為了在輸出標註是分析哪個 app。"
                        ),
                    },
                    "launch_app": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "搭配 app_id：True 時在 hierarchy dump 前用 maestro launchApp 啟動 app。"
                            "用 clearState: false（保留 app 狀態），確保看到「真實」起始畫面。"
                            "省略則假設裝置上 app 已是當前前景。"
                        ),
                    },
                    "timeout_ms": {
                        "type": "integer",
                        "default": 30000,
                        "description": (
                            "選填，hierarchy 命令超時毫秒。預設 30000；"
                            "BlueStacks / 遠端 ADB 較慢，QA_ANDROID_HOST 有設時會自動拉到 60000 起跳。"
                        ),
                    },
                },
            },
        ),
        Tool(
            name="init_qa_knowledge",
            description=(
                "在受測專案根 (PROJECT_ROOT) 建立 qa-knowledge.md 起手範本，"
                "含業務規則 / 歷史 Bug / 標準斷言文字 / User Journeys / 技術約束 5 個 H2 區段，"
                "每段都有 TODO 提示。Idempotent：檔已存在不會覆蓋（除非 overwrite=true）。"
                "新用戶建議第一次跑 MCP 就先 call 一次。"
                "這份檔案後續會被 get_qa_context 讀、做為 business_context 傳進 generate_test，"
                "讓 AI 寫出有業務邏輯的測試（而不是泛例 monkey testing）。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "overwrite": {
                        "type": "boolean",
                        "default": False,
                        "description": "強制覆蓋既存檔案（會丟失你已填的內容、請先備份）",
                    },
                },
            },
        ),
        Tool(
            name="get_qa_context",
            description=(
                "讀取受測專案的 qa-knowledge.md（業務規則 / 歷史 Bug / 標準斷言文字 / "
                "User Journeys 等領域知識），用 ## H2 區段拆分。"
                "用法：先 call 拿到整份或指定 section，再把相關段落以 business_context "
                "傳給 generate_test，產出的 test 就會自帶業務知識註解 — 跳脫 monkey testing。"
                "若檔案不存在會 fallback 到內建的 ISTQB 七大原則 + 等價分割 + 邊界值 + 決策表 + "
                "狀態轉換 + Mobile checklist 通用知識，先用著也可以；之後跑 init_qa_knowledge 建立專案專屬版本。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "section": {
                        "type": "string",
                        "description": (
                            "選填，只取單一 H2 section（不區分大小寫、支援部分匹配）。"
                            "省略則回整份檔 + 所有 section 名稱清單。"
                        ),
                    },
                },
            },
        ),
        Tool(
            name="auto_generate_tests",
            description=(
                "一鍵交付：在內部依序做 analyze_url → 為每個偵測到的 module 用 candidate_tcs 內容"
                "各跑一次 generate_test，把整套 pytest 測試骨架寫進 PROJECT_ROOT/tests/。"
                "等同於『analyze_url 後對每個 module 手動跑 N 次 generate_test』的自動化版本，"
                "適合「給我一個 URL、其他你看著辦」這種快速覆蓋場景。每條 candidate_tc 變成對應"
                " test 函式的 docstring，run_tests 跑完 HTML 報告會用 docstring 當 case 名稱顯示。"
                "回傳產生的檔案路徑列表 + 每個 module 對應幾個 test。預設每個 module 1 條，"
                "想要更密的覆蓋拉 tests_per_module。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "要分析並批次產測的 URL，需含 protocol（http:// 或 https://）。",
                    },
                    "timeout_ms": {
                        "type": "integer",
                        "default": 15000,
                        "description": (
                            "選填，analyze_url 內部 page.goto 等 DOMContentLoaded 的逾時毫秒。"
                            "預設 15000，慢站可拉到 30000+。"
                        ),
                    },
                    "auth_cookie": {
                        "type": "string",
                        "description": (
                            "選填，登入後分析所需 cookie，格式：`name1=value1; name2=value2`。"
                            "從 DevTools / Application / Cookies 抓現成值貼進來。"
                        ),
                    },
                    "tests_per_module": {
                        "type": "integer",
                        "default": 1,
                        "minimum": 1,
                        "maximum": 10,
                        "description": (
                            "選填，每個 module 從 candidate_tcs 取前 N 條各產一條 test。"
                            "1-10，預設 1（最少噪音）。想要更密的覆蓋拉 3-5；"
                            "拉到 10 通常會產 garbage tests，因為 candidate_tcs 後段是泛例。"
                        ),
                    },
                },
                "required": ["url"],
            },
        ),
    ]


@app.list_resources()
async def list_resources() -> list[Resource]:
    """提供測試報告作為 MCP resource，AI 編輯器可即時讀取。"""
    return [
        Resource(
            uri=AnyUrl("report://html"),
            name="Latest Test Report (HTML)",
            description="最近一次測試報告，即時渲染為自包含 HTML",
            mimeType="text/html",
        ),
        Resource(
            uri=AnyUrl("report://json"),
            name="Latest Test Report (JSON)",
            description="原始 report.json（各 runner 的原生格式）",
            mimeType="application/json",
        ),
        Resource(
            uri=AnyUrl("report://optimization"),
            name="Optimization Plan (Markdown)",
            description="自我強化分析：每跑完一次自動產出的下一輪行動清單",
            mimeType="text/markdown",
        ),
    ]


@app.read_resource()
async def read_resource(uri: AnyUrl) -> str:
    uri_str = str(uri)
    if uri_str == "report://html":
        return html_reporter.render_report()
    if uri_str == "report://json":
        if not REPORT_PATH.exists():
            return "{}"
        return REPORT_PATH.read_text(encoding="utf-8")
    if uri_str == "report://optimization":
        if not OPTIMIZATION_PATH.exists():
            optimizer.write_plan()
        if OPTIMIZATION_PATH.exists():
            return OPTIMIZATION_PATH.read_text(encoding="utf-8")
        return "# Optimization Plan\n\n_目前沒有歷史資料可分析。先跑一次 run_tests。_"
    raise ValueError(f"未知的 resource URI: {uri_str}")


@app.call_tool()
async def call_tool(name: str, args: dict) -> list[TextContent]:
    started = time.time()
    err_type: str | None = None
    try:
        return await _dispatch(name, args)
    except Exception as e:
        err_type = type(e).__name__
        return [TextContent(type="text", text=f"執行錯誤: {err_type}: {e}")]
    finally:
        # Telemetry feeds the optimizer's MCP-usability analysis. Best-effort —
        # never break a tool call because logging failed.
        telemetry.log_tool_call(name, args or {}, int((time.time() - started) * 1000), err_type)


async def _dispatch(name: str, args: dict) -> list[TextContent]:
    if name == "get_runner_info":
        info = {
            "current": get_runner().name,
            "available": sorted(set(r.name for r in REGISTRY.values())),
        }
        return [TextContent(type="text", text=json.dumps(info, ensure_ascii=False, indent=2))]

    if name == "list_tests":
        return [TextContent(type="text", text=runner.list_tests())]

    if name == "run_tests":
        result = runner.run_tests(
            filter=args.get("filter"),
            headed=args.get("headed", False),
            browser=args.get("browser", "chromium"),
        )
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

    if name == "run_failed":
        result = runner.run_failed()
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

    if name == "get_test_report":
        result = reporter.get_report_summary()
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

    if name == "get_failure_details":
        result = reporter.get_failure_details(args.get("test_id"))
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

    if name == "generate_test":
        module = args.get("module")
        msg = generator.generate_test(
            args["description"],
            args["filename"],
            url=args.get("url"),
            module=module,
            business_context=args.get("business_context"),
        )
        # Tagging the source lets the optimizer track "URL → AI-generated → adopted"
        # adoption rate per analyze_url module.
        if isinstance(module, dict) and module.get("name"):
            source = f"analyze_url:{module['name']}"
        else:
            source = "manual"
        telemetry.log_generation(args["filename"], args.get("description", ""), source=source)
        return [TextContent(type="text", text=msg)]

    if name == "get_qa_context":
        result = qa_context.load_context(args.get("section"))
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

    if name == "init_qa_knowledge":
        result = qa_context.init_qa_knowledge(overwrite=args.get("overwrite", False))
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

    if name == "codegen":
        msg = generator.codegen(args["url"], args.get("output", "recorded_test.py"))
        return [TextContent(type="text", text=msg)]

    if name == "generate_html_report":
        target = html_reporter.write_report(args.get("output", "report.html"))
        return [TextContent(type="text", text=f"已產生 HTML 報告：{target}")]

    if name == "get_test_history":
        result = reporter.get_history(args.get("limit", 10))
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

    if name == "get_optimization_plan":
        plan = optimizer.build_plan(
            history_limit=args.get("history_limit", 10),
            telemetry_limit=args.get("telemetry_limit", 500),
        )
        optimizer.write_plan(plan)
        return [TextContent(type="text", text=json.dumps(plan, ensure_ascii=False, indent=2))]

    if name == "analyze_url":
        result = await analyzer.analyze_url(
            args["url"],
            timeout_ms=args.get("timeout_ms", 15000),
            auth_cookie=args.get("auth_cookie"),
        )
        if isinstance(result, dict) and "error" not in result:
            telemetry.log_discovered_modules(args["url"], result.get("modules", []))
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

    if name == "analyze_screen":
        # Sync subprocess call — wrapped in to_thread so it doesn't block the
        # MCP server's asyncio loop while maestro CLI runs.
        result = await asyncio.to_thread(
            analyzer.analyze_screen,
            args.get("app_id"),
            args.get("launch_app", False),
            args.get("timeout_ms", 30000),
        )
        # Telemetry: log discovered modules with the app_id as the "url"
        # so the optimizer's coverage-gap analysis covers mobile screens too.
        if isinstance(result, dict) and "error" not in result:
            telemetry.log_discovered_modules(
                args.get("app_id") or "screen", result.get("modules", []),
            )
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

    if name == "auto_generate_tests":
        result = await _auto_generate_tests(
            url=args["url"],
            timeout_ms=args.get("timeout_ms", 15000),
            auth_cookie=args.get("auth_cookie"),
            tests_per_module=args.get("tests_per_module", 1),
        )
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

    return [TextContent(type="text", text=f"未知的 tool: {name}")]


async def _auto_generate_tests(
    url: str,
    timeout_ms: int,
    auth_cookie: str | None,
    tests_per_module: int,
) -> dict:
    """analyze_url → per module → generate_test × N. All-in-one orchestration.

    Why inline in server.py: the chain is short and stays close to where the
    individual tools are already wired. Telemetry hooks mirror the manual path
    so the optimizer still sees the same discovery + generation signals.
    """
    analysis = await analyzer.analyze_url(
        url, timeout_ms=timeout_ms, auth_cookie=auth_cookie,
    )
    if isinstance(analysis, dict) and "error" in analysis:
        return analysis
    if isinstance(analysis, dict):
        telemetry.log_discovered_modules(url, analysis.get("modules", []) or [])

    generated: list[dict] = []
    for module in (analysis.get("modules", []) or []):
        candidates = module.get("candidate_tcs", []) or []
        module_name = module.get("name", "module")
        for i, tc in enumerate(candidates[:tests_per_module]):
            slug = f"{module_name}_{i}" if i > 0 else module_name
            try:
                generator.generate_test(
                    description=tc,
                    filename=slug,
                    url=url,
                    module=module,
                )
                file_out = f"test_{slug}.py"
                generated.append({
                    "filename": file_out,
                    "description": tc,
                    "module_kind": module.get("kind"),
                    "module_name": module_name,
                })
                telemetry.log_generation(
                    file_out, tc, source=f"auto_generate_tests:{module_name}",
                )
            except Exception as e:
                generated.append({
                    "filename": f"test_{slug}.py",
                    "module_name": module_name,
                    "error": f"{type(e).__name__}: {e}",
                })

    return {
        "url": url,
        "page_title": analysis.get("page_title"),
        "module_count": analysis.get("module_count"),
        "api_endpoint_count": analysis.get("api_endpoint_count"),
        "tests_generated": sum(1 for g in generated if "error" not in g),
        "tests_failed": sum(1 for g in generated if "error" in g),
        "tests": generated,
    }


async def main():
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())


def run():
    asyncio.run(main())


if __name__ == "__main__":
    run()
