# TestBridge Runner

> Universal MCP server for running tests across pytest / Jest / Cypress / Go.
> Official local execution engine for [TestBridge](https://github.com/kao273183/testbridge).

基於 **MCP（Model Context Protocol）** 的通用測試自動化伺服器。透過 plugin 架構支援多種測試框架，靠環境變數切換 runner。

| `QA_RUNNER` | 框架 | 語言 |
|---|---|---|
| `pytest` / `pytest-playwright` / `playwright` | pytest + Playwright | Python |
| `jest` | Jest | JavaScript |
| `cypress` | Cypress | JavaScript |
| `go` / `go-test` | `go test` | Go |

完整設計請見 [`framework.md`](framework.md)。

---

## 為什麼有這個專案？

**TestBridge** 是雲端 QA SaaS（管 test case / runs / bugs / 媒合），但雲端摸不到你本機的測試專案。
**TestBridge Runner** 補上「在你電腦本機執行測試」的最後一哩路：

```
TestBridge (web SaaS)  ──→  管理 / 視覺化 / 報表
TestBridge Runner (本機 MCP)  ──→  跑 pytest / jest / cypress / go test
        ↑
        Claude Desktop / Cursor 直接呼叫
```

兩端透過 HTTP API 同步測試結果（roadmap 中）。

---

## 安裝

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
playwright install   # 只有用 pytest-playwright 時需要
```

## 接到 Claude Desktop

複製 `claude_desktop_config.example.json` 到：

- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

兩個關鍵環境變數：

| 變數 | 範例 | 說明 |
|---|---|---|
| `QA_RUNNER` | `pytest` / `jest` / `cypress` / `go` | 選擇測試框架 |
| `QA_PROJECT_ROOT` | `/path/to/your/project` | 受測專案根目錄 |

### 切換 runner 範例

**pytest-playwright**:
```json
"env": {
  "QA_RUNNER": "pytest",
  "QA_PROJECT_ROOT": "/path/to/python-project"
}
```

**Jest**:
```json
"env": {
  "QA_RUNNER": "jest",
  "QA_PROJECT_ROOT": "/path/to/node-project"
}
```

**Cypress**:
```json
"env": {
  "QA_RUNNER": "cypress",
  "QA_PROJECT_ROOT": "/path/to/cypress-project"
}
```

**Go test**:
```json
"env": {
  "QA_RUNNER": "go",
  "QA_PROJECT_ROOT": "/path/to/go-project"
}
```

---

## Tool 清單（所有 runner 共用同一組）

| Tool | 用途 |
|---|---|
| `get_runner_info` | 看目前用哪個 runner、有哪些可用 |
| `list_tests` | 列出所有測試 |
| `run_tests` | 執行測試（filter、headed、browser；後兩者只 pytest-playwright 用） |
| `run_failed` | 重跑上次失敗 |
| `get_test_report` | 報告摘要 |
| `get_failure_details` | 失敗詳情 |
| `generate_test` | 從描述產生對應框架的測試骨架 |
| `codegen` | 啟動 Playwright codegen（其他 runner 不支援） |

---

## 專案結構

```
testbridge-runner/
├── pyproject.toml
├── src/testbridge_runner/
│   ├── server.py            # MCP 入口（tool 路由）
│   ├── config.py            # 環境變數
│   ├── runners/             # 各框架實作（plugin）
│   │   ├── base.py          # TestRunner 抽象介面
│   │   ├── pytest_playwright.py
│   │   ├── jest.py
│   │   ├── cypress.py
│   │   └── go_test.py
│   ├── tools/               # server.py → runner 的薄層 delegate
│   └── resources/           # report:// 與 trace:// 資源
└── tests_project/           # 受測專案範例（pytest+playwright）
```

---

## 新增一個 runner

1. 在 `src/testbridge_runner/runners/` 新增 `your_runner.py`，繼承 `TestRunner`，實作 6 個 abstract method
2. 在 `runners/__init__.py` 的 `REGISTRY` 註冊名稱
3. 完成 ✅

---

## 使用範例

對 Claude 說：

> 「現在用哪個 runner？」→ `get_runner_info`
> 「跑一下所有測試」→ `run_tests`
> 「剛剛哪幾個失敗了？」→ `get_failure_details`
> 「幫我寫一個登入測試」→ `generate_test`（自動產生對應語言的骨架）

---

## Related Projects

- **[TestBridge](https://github.com/kao273183/testbridge)** — 主產品，雲端 QA 協作 SaaS（Next.js + Firebase + Claude API）

## License

MIT © 2026 Chenjun Digital · Jack Kao
