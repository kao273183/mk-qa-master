# QA Test Automation MCP Server

一個基於 Model Context Protocol (MCP) 的測試自動化伺服器，整合 Playwright / Selenium / Cypress，讓 Claude 可以直接執行測試、分析失敗原因、產生 test case。

---

## 目錄

- [設計思路](#設計思路)
- [Tool 清單](#tool-清單)
- [專案結構](#專案結構)
- [完整程式碼](#完整程式碼)
- [安裝與設定](#安裝與設定)
- [使用範例](#使用範例)
- [擴充方向](#擴充方向)

---

## 設計思路

MCP 三大原語在 QA 場景的對應：

| MCP 原語 | QA 場景 | 範例 |
|---|---|---|
| **Tools**（動作） | 執行測試、產生 case、重跑、截圖 | `run_tests`, `generate_test` |
| **Resources**（資料） | 測試報告、screenshot、trace 檔 | `report://latest`, `trace://test-id` |
| **Prompts**（範本） | 標準化的測試任務 | 「從 user story 產生測試」 |

### 為什麼選 Playwright

- Python 官方支援良好（`pytest-playwright`）
- 內建 trace viewer，失敗除錯方便
- `codegen` 可錄製操作產生程式碼
- 多瀏覽器（Chromium / Firefox / WebKit）

> Selenium、Cypress 同樣可以包，本文件附加實作見[擴充方向](#擴充方向)。

---

## Tool 清單

| Tool | 用途 | 主要參數 |
|---|---|---|
| `list_tests` | 列出所有測試（含 tag、路徑） | - |
| `run_tests` | 執行測試 | `filter`, `headed`, `browser` |
| `run_failed` | 只重跑上次失敗的 | - |
| `get_test_report` | 回傳最近一次報告摘要 | - |
| `get_failure_details` | 給定 test id，回傳錯誤、stack、截圖 | `test_id` |
| `generate_test` | 從自然語言描述產生測試檔 | `description`, `filename` |
| `codegen` | 包裝 `playwright codegen` 錄製 | `url`, `output` |
| `update_snapshot` | 更新視覺回歸 baseline | `test_id` |

---

## 專案結構

```
mcp-test-runner/
├── pyproject.toml
├── README.md
├── docs/                              # framework.md, qa-knowledge.example.md
├── examples/configs/                  # client config examples
├── src/
│   └── mcp_test_runner/
│       ├── __init__.py
│       ├── server.py            # MCP 入口
│       ├── config.py            # 設定（受測專案路徑等）
│       ├── tools/
│       │   ├── __init__.py
│       │   ├── runner.py        # 測試執行
│       │   ├── reporter.py      # 解析 JSON report
│       │   └── generator.py     # 產生 test 檔
│       └── resources/
│           ├── __init__.py
│           └── reports.py       # 提供報告資源
└── tests_project/                # 受測專案（範例）
    ├── conftest.py
    └── test_example.py
```

---

## 完整程式碼

### `pyproject.toml`

```toml
[project]
name = "mcp-test-runner"
version = "0.1.0"
description = "QA Test Automation MCP Server"
requires-python = ">=3.10"
dependencies = [
    "mcp>=1.0.0",
    "playwright>=1.40.0",
    "pytest>=8.0.0",
    "pytest-playwright>=0.4.0",
    "pytest-json-report>=1.5.0",
]

[project.scripts]
mcp-test-runner = "mcp_test_runner.server:run"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

---

### `src/mcp_test_runner/config.py`

```python
from pathlib import Path
import os

# 受測專案根目錄（可用環境變數覆寫）
PROJECT_ROOT = Path(os.getenv("QA_PROJECT_ROOT", "./tests_project")).resolve()

# 報告與 artifact 路徑
REPORT_PATH = PROJECT_ROOT / "report.json"
ARTIFACTS_DIR = PROJECT_ROOT / "test-results"
```

---

### `src/mcp_test_runner/tools/runner.py`

```python
import subprocess
from pathlib import Path
from ..config import PROJECT_ROOT, REPORT_PATH


def list_tests() -> str:
    """列出所有 pytest 測試（collect-only）"""
    result = subprocess.run(
        ["pytest", "--collect-only", "-q"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    return result.stdout or result.stderr


def run_tests(
    filter: str | None = None,
    headed: bool = False,
    browser: str = "chromium",
) -> dict:
    """執行測試。回傳 exit code 與輸出尾段"""
    cmd = [
        "pytest",
        f"--browser={browser}",
        "--json-report",
        f"--json-report-file={REPORT_PATH}",
    ]
    if headed:
        cmd.append("--headed")
    if filter:
        cmd.extend(["-k", filter])

    result = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True)
    return {
        "exit_code": result.returncode,
        "stdout_tail": result.stdout[-2000:],
        "stderr_tail": result.stderr[-1000:],
    }


def run_failed() -> dict:
    """只重跑上次失敗"""
    result = subprocess.run(
        ["pytest", "--lf", "--json-report", f"--json-report-file={REPORT_PATH}"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    return {"exit_code": result.returncode, "stdout_tail": result.stdout[-2000:]}
```

---

### `src/mcp_test_runner/tools/reporter.py`

```python
import json
from ..config import REPORT_PATH


def get_report_summary() -> dict:
    """回傳報告摘要"""
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


def get_failure_details(test_id: str | None = None) -> list[dict]:
    """取得失敗詳細資訊"""
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
```

---

### `src/mcp_test_runner/tools/generator.py`

```python
from pathlib import Path
import subprocess
from ..config import PROJECT_ROOT


TEST_TEMPLATE = '''"""
{description}
"""
from playwright.sync_api import Page, expect


def test_{slug}(page: Page):
    # TODO: 由 Claude 補完實作
    page.goto("https://example.com")
    expect(page).to_have_title("Example Domain")
'''


def generate_test(description: str, filename: str) -> str:
    """產生測試骨架檔。Claude 後續可呼叫檔案編輯工具補完內容。"""
    if not filename.startswith("test_"):
        filename = f"test_{filename}"
    if not filename.endswith(".py"):
        filename += ".py"

    slug = filename.replace("test_", "").replace(".py", "")
    content = TEST_TEMPLATE.format(description=description, slug=slug)

    target = PROJECT_ROOT / filename
    target.write_text(content)
    return f"已產生 {target}，內容：\n\n{content}"


def codegen(url: str, output: str = "recorded_test.py") -> str:
    """錄製操作產生程式碼（會開瀏覽器）"""
    target = PROJECT_ROOT / output
    subprocess.run(
        ["playwright", "codegen", "-o", str(target), url],
        cwd=PROJECT_ROOT,
    )
    return f"錄製完成，已存至 {target}"
```

---

### `src/mcp_test_runner/server.py`

```python
import asyncio
import json
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from .tools import runner, reporter, generator

app = Server("mcp-test-runner")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
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
```

---

### `tests_project/conftest.py`（受測專案範例）

```python
import pytest
from playwright.sync_api import Page


@pytest.fixture(autouse=True)
def setup(page: Page):
    page.set_default_timeout(5000)
    yield
```

---

### `tests_project/test_example.py`

```python
from playwright.sync_api import Page, expect


def test_homepage_title(page: Page):
    page.goto("https://example.com")
    expect(page).to_have_title("Example Domain")


def test_link_visible(page: Page):
    page.goto("https://example.com")
    expect(page.get_by_role("link", name="More information...")).to_be_visible()
```

---

## 安裝與設定

### 1. 環境準備

```bash
# 建立專案
mkdir mcp-test-runner && cd mcp-test-runner

# 建立虛擬環境
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 安裝依賴
pip install -e .
playwright install
```

### 2. 接到 Claude Desktop

編輯 `claude_desktop_config.json`：

**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "mcp-test-runner": {
      "command": "python",
      "args": ["-m", "mcp_test_runner.server"],
      "cwd": "/absolute/path/to/mcp-test-runner",
      "env": {
        "QA_PROJECT_ROOT": "/absolute/path/to/mcp-test-runner/tests_project"
      }
    }
  }
}
```

重啟 Claude Desktop，左下角應該看到 `mcp-test-runner` 的工具圖示。

---

## 使用範例

接好之後，可以這樣跟 Claude 對話：

> 「列出所有測試」
> → Claude 呼叫 `list_tests`

> 「跑一下所有測試，用 firefox」
> → Claude 呼叫 `run_tests(browser="firefox")`

> 「剛剛哪幾個失敗了？給我細節」
> → Claude 呼叫 `get_failure_details`，分析錯誤

> 「幫我寫一個測試：登入頁面，輸入錯誤密碼應該顯示錯誤訊息」
> → Claude 呼叫 `generate_test`，再用檔案編輯工具補完內容

> 「重跑失敗的，這次用 headed 模式」
> → Claude 呼叫 `run_failed`

---

## 擴充方向

### 1. Trace 整合
Playwright 失敗時會產生 `trace.zip`，可加 resource：
```python
@app.list_resources()
async def list_resources():
    return [Resource(uri=f"trace://{p.stem}", name=p.name)
            for p in ARTIFACTS_DIR.glob("**/trace.zip")]
```

### 2. 視覺回歸
包裝 `expect(page).to_have_screenshot()`，新增 `update_snapshot` tool 讓 Claude 看 diff 圖判斷是否要更新 baseline。

### 3. 自我修復 Selector
失敗時用 Playwright 的 `page.content()` 拿 DOM，讓 Claude 分析並建議新 selector。

### 4. Selenium 版本
把 `runner.py` 改成呼叫 pytest + `pytest-selenium`，或直接用 `unittest`：
```python
cmd = ["pytest", "--driver", "Chrome", ...]
```

### 5. Cypress 版本（Node.js）
Cypress 是 JS 工具，從 Python 用 subprocess 包：
```python
subprocess.run(["npx", "cypress", "run", "--spec", spec_path], ...)
```
報告解析改讀 `cypress/reports/*.json`。

### 6. CI 整合
新增 tool 從 GitHub Actions API 拉最近 workflow 的失敗報告，讓 Claude 分析 CI 紅燈原因。

### 7. Prompts
加入標準化 prompt 範本：
```python
@app.list_prompts()
async def list_prompts():
    return [Prompt(
        name="analyze_failure",
        description="分析測試失敗原因並建議修復",
    )]
```

---

## 參考資源

- [MCP 官方文件](https://modelcontextprotocol.io)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [Playwright Python](https://playwright.dev/python/)
- [pytest-playwright](https://github.com/microsoft/playwright-pytest)
