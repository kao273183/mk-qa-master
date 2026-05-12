import asyncio
import json
import time
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, Resource
from pydantic import AnyUrl

from .tools import runner, reporter, generator, analyzer, telemetry, optimizer
from .runners import get_runner, REGISTRY
from .reporters import html as html_reporter
from .config import REPORT_PATH, OPTIMIZATION_PATH

app = Server("testbridge-runner")


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
                "產生測試骨架。若提供 url+module（來自 analyze_url），會用 selectors 預填出"
                "可直接執行的版本，並把 candidate_tcs 寫成註解；否則回退到通用骨架模板。"
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
    ]


@app.list_resources()
async def list_resources() -> list[Resource]:
    """提供測試報告作為 MCP resource，AI 編輯器可即時讀取。"""
    return [
        Resource(
            uri=AnyUrl("report://html"),
            name="Latest Test Report (HTML)",
            description="最近一次測試報告，即時渲染為 TestBridge 風格 HTML",
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
        )
        # Tagging the source lets the optimizer track "URL → AI-generated → adopted"
        # adoption rate per analyze_url module.
        if isinstance(module, dict) and module.get("name"):
            source = f"analyze_url:{module['name']}"
        else:
            source = "manual"
        telemetry.log_generation(args["filename"], args.get("description", ""), source=source)
        return [TextContent(type="text", text=msg)]

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

    return [TextContent(type="text", text=f"未知的 tool: {name}")]


async def main():
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())


def run():
    asyncio.run(main())


if __name__ == "__main__":
    run()
