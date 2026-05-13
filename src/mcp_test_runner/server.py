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
            description="回傳目前使用的測試 runner 名稱與所有可用 runner",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="list_tests",
            description="列出受測專案內所有 Playwright/pytest 測試",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="run_tests",
            description="執行測試。可用 filter 篩選、選擇 headed 模式與瀏覽器",
            inputSchema={
                "type": "object",
                "properties": {
                    "filter": {
                        "type": "string",
                        "description": "測試名稱關鍵字（pytest -k）",
                    },
                    "headed": {"type": "boolean", "default": False},
                    "browser": {
                        "type": "string",
                        "enum": ["chromium", "firefox", "webkit"],
                        "default": "chromium",
                    },
                },
            },
        ),
        Tool(
            name="run_failed",
            description="只重跑上次失敗的測試（pytest --lf）",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_test_report",
            description="取得最近一次測試報告的摘要（pass/fail 統計）",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_failure_details",
            description="取得失敗測試的詳細錯誤訊息與 stack trace",
            inputSchema={
                "type": "object",
                "properties": {
                    "test_id": {
                        "type": "string",
                        "description": "選填，特定 test 的 nodeid 關鍵字",
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
                    "description": {"type": "string"},
                    "filename": {"type": "string"},
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
            description="啟動 Playwright codegen 錄製操作（會開瀏覽器視窗）",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "output": {"type": "string", "default": "recorded_test.py"},
                },
                "required": ["url"],
            },
        ),
        Tool(
            name="generate_html_report",
            description="把最近一次測試結果渲染成自包含 HTML 報告，儲存至受測專案根目錄",
            inputSchema={
                "type": "object",
                "properties": {
                    "output": {
                        "type": "string",
                        "default": "report.html",
                        "description": "輸出檔名（相對於 QA_PROJECT_ROOT）",
                    },
                },
            },
        ),
        Tool(
            name="get_test_history",
            description=(
                "回傳最近 N 次測試 run 的摘要（時間戳、pass/fail/skipped、duration、pass rate），"
                "用於檢視 flake 與趨勢。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 100},
                },
            },
        ),
        Tool(
            name="get_optimization_plan",
            description=(
                "綜合 run history、telemetry、analyze_url 紀錄，產出三層自我強化分析："
                "(1) 測試套件品質：flake / broken / slow_regression / stable_passing；"
                "(2) MCP 使用模式：高頻 tool、重複呼叫、錯誤率、常見鏈；"
                "(3) AI 產測效益：generate_test 採用率、analyze_url 覆蓋缺口。"
                "回傳 JSON 並同步寫成 optimization-plan.md。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "history_limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 100},
                    "telemetry_limit": {"type": "integer", "default": 500, "minimum": 10, "maximum": 5000},
                },
            },
        ),
        Tool(
            name="analyze_url",
            description=(
                "開啟網頁、自動拆解可測模塊（form / nav / dialog / labeled section / CTA），"
                "並為每個模塊提出候選 TC 清單。給 AI 編輯器作為設計測試的素材，"
                "後續可餵給 generate_test 寫骨架。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要分析的網頁 URL"},
                    "timeout_ms": {
                        "type": "integer",
                        "default": 15000,
                        "description": "page.goto 的逾時毫秒數",
                    },
                    "auth_cookie": {
                        "type": "string",
                        "description": "選填，預先注入登入 cookie，格式：name1=value1; name2=value2",
                    },
                },
                "required": ["url"],
            },
        ),
        Tool(
            name="analyze_screen",
            description=(
                "Mobile 版的 analyze_url：dump 當前 iOS Simulator / Android Emulator / 真機"
                "前景 app 的 screen hierarchy（透過 `maestro hierarchy`）並轉成可測 modules"
                "（inputs / CTAs / tab bar）+ candidate TCs。需 Maestro CLI 已裝、且裝置 booted、app 在前景。"
                "若提供 app_id + launch_app=true 會先啟動該 app。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "app_id": {
                        "type": "string",
                        "description": "選填，bundle id / package name（如 com.example.app）",
                    },
                    "launch_app": {
                        "type": "boolean",
                        "default": False,
                        "description": "搭配 app_id 使用：true 時 dump 前先 launchApp",
                    },
                    "timeout_ms": {"type": "integer", "default": 30000},
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
                "一鍵交付：分析 URL → 為每個偵測到的模塊用其 candidate_tcs 自動產出對應 pytest 測試。"
                "等同於『analyze_url → 對每個 module 連跑 generate_test』，"
                "適合『給 URL、其他自動』的快速覆蓋場景。"
                "每條 candidate_tc 會變成對應 test 的 docstring，"
                "之後 run_tests 跑完、HTML 報告就會用這些 docstring 當 case 名稱。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要分析並產測的 URL"},
                    "timeout_ms": {"type": "integer", "default": 15000},
                    "auth_cookie": {
                        "type": "string",
                        "description": "選填，登入 cookie，格式：name1=value1; name2=value2",
                    },
                    "tests_per_module": {
                        "type": "integer",
                        "default": 1,
                        "minimum": 1,
                        "maximum": 10,
                        "description": "每個模塊從 candidate_tcs 取前 N 條各產一條 test（預設 1）",
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
