import asyncio
import json
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from .tools import runner, reporter, generator
from .runners import get_runner, REGISTRY

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
            description="根據描述產生 Playwright 測試檔骨架",
            inputSchema={
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "filename": {"type": "string"},
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
    ]


@app.call_tool()
async def call_tool(name: str, args: dict) -> list[TextContent]:
    try:
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
            msg = generator.generate_test(args["description"], args["filename"])
            return [TextContent(type="text", text=msg)]

        if name == "codegen":
            msg = generator.codegen(args["url"], args.get("output", "recorded_test.py"))
            return [TextContent(type="text", text=msg)]

        return [TextContent(type="text", text=f"未知的 tool: {name}")]

    except Exception as e:
        return [TextContent(type="text", text=f"執行錯誤: {type(e).__name__}: {e}")]


async def main():
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())


def run():
    asyncio.run(main())


if __name__ == "__main__":
    run()
