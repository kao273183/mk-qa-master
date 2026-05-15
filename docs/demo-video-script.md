# Demo Video Script — mk-qa-master

兩個版本：**A. 60 秒社群版**（X / LinkedIn / Reddit / PH 預告），**B. 3 分鐘完整版**（YouTube / Smithery listing 嵌入）。同一次錄製可以一鏡到底拍滿 3 分鐘，再剪出 60 秒。

---

## 共用前置（錄前 10 分鐘搞定）

| 項目 | 設定 |
|---|---|
| 解析度 | 1920×1080 或 1440p（YouTube 推薦），16:9 |
| 字型大小 | terminal 與 IDE 都調到平時的 1.4–1.6 倍，手機可讀 |
| 字體 | 等寬建議 JetBrains Mono / Fira Code，行距放鬆 |
| 主題 | 深色背景一致（VS Code Dark+ 或 Tokyo Night） |
| 錄製 | macOS 用 Screen Studio（自動 zoom + 滑鼠 highlight，最省事）；免費替代 OBS |
| 麥克風 | 有外接最好；沒有就 AirPods 加事後 noise gate |
| 受測站 | 用 `https://the-internet.herokuapp.com/login`（公開、永遠在、簡單）或 `https://demoqa.com` |
| Claude 模型 | Claude Desktop + Sonnet/Opus，視窗開到 60% 寬，留空間給 terminal |
| 螢幕配置 | 左：Claude Desktop ／ 右：terminal + `tests_project/` 檔案總管 |

**警告**：開錄前清掉 `tests_project/test-results/`、`report.json`、`junit.xml`，免得歷史污染畫面。

---

## A. 60 秒社群版（hook 用，目標：點進 GitHub）

> 重點：**第 8 秒前必須出現「魔法時刻」**，否則滑掉。先拍效果、再解釋是什麼。

### 分鏡

| 時間 | 畫面 | 旁白 / 字卡 |
|---|---|---|
| 0:00–0:03 | 黑底白字卡：「**Tell Claude to test your app. It writes the test, runs it, reports back.**」 | 無旁白 |
| 0:03–0:10 | Claude Desktop。使用者輸入：`Analyze https://the-internet.herokuapp.com/login and write a Playwright test for the login form, then run it.` | 「One sentence. One MCP.」 |
| 0:10–0:25 | Claude 依序呼叫 `analyze_url` → 出現模組列表 → `generate_test` → 在右側 IDE 跳出新檔 `test_login_form.py` | 「It probes the DOM, picks selectors, writes a runnable Playwright test.」 |
| 0:25–0:40 | Claude 呼叫 `run_tests` → terminal 跑出 pytest passing → Claude 回 summary | 「Then it runs it. Real browser. Real assertions.」 |
| 0:40–0:50 | Claude 呼叫 `generate_html_report` → 瀏覽器自動開啟 HTML 報告 | 「One self-contained HTML report you can ship to Slack.」 |
| 0:50–0:58 | 切五個 logo 拼貼：pytest / Jest / Cypress / Go / Maestro，下方字卡：「**Same loop. 5 runners. Web + mobile.**」 | 「Works across pytest, Jest, Cypress, Go test, and Maestro.」 |
| 0:58–1:00 | 結尾卡：`smithery.ai/server/kao273183/mk-qa-master` + GitHub URL | 無旁白 |

### 60 秒版貼文模板（X / LinkedIn）

```
我把 QA 工作流塞進一個 MCP。

對 Claude 說：「分析這個登入頁，幫我寫 Playwright 測試並跑起來。」
→ 它探 DOM、挑 selector、寫出可跑的 .py、執行、回報告。

同一條 loop 支援 pytest / Jest / Cypress / Go / Maestro（含手機）。

GitHub: github.com/kao273183/mk-qa-master
Smithery: smithery.ai/server/kao273183/mk-qa-master
```

---

## B. 3 分鐘完整版（深度版，目標：installs / stars）

> 結構：**Hook（30s）→ Web 流程（60s）→ Mobile 流程（45s）→ 自我改善（30s）→ Outro（15s）**

### 0:00–0:30 Hook + 痛點

- 字卡：「**寫測試很煩。維護測試更煩。**」
- 旁白：「你打開 Playwright codegen、點半天、產一坨難讀的 code、複製貼上、改 selector、跑、紅、再改。下次 UI 動了，再來一輪。」
- 切 Claude Desktop：「但 LLM 有 MCP 以後不該是這樣。」
- 字卡：「**mk-qa-master — 一個 MCP，五個 runner，從 analyze 到 coach。**」

### 0:30–1:30 Web 流程（pytest-playwright）

| 時間 | 操作 | 旁白要點 |
|---|---|---|
| 0:30 | 在 Claude 輸入 `Analyze https://the-internet.herokuapp.com/login` | 介紹 `analyze_url` 不只抓 HTML，會 probe DOM、列模組、給 selector、列 API endpoint、抓 overflow |
| 0:50 | Claude 回模組列表（form / cta / link 等），畫面 highlight 那個 `email_password_form_0` | 「它認得這是登入表單，給了模組 ID。」 |
| 1:00 | 接續輸入 `generate a test for the login form and run it` | 解釋 `generate_test` 拿 module 當輸入會產 *runnable* 測試，不是骨架 |
| 1:10 | 跳右側 IDE：新檔 `tests_project/test_login_form.py` 出現，有 fixture、有 selector、有 assert | 「不是 skeleton，是真的能跑的測試。」 |
| 1:20 | 切 terminal：pytest 跑出 `1 passed in 2.3s` | 「然後它跑起來——真實瀏覽器、真實 assertion。」 |

### 1:30–2:15 Mobile 流程（Maestro + BlueStacks 或模擬器）

| 時間 | 操作 | 旁白要點 |
|---|---|---|
| 1:30 | 切配置：`QA_RUNNER=maestro`，模擬器已開 | 「同樣的工具，換個 env。」 |
| 1:40 | 在 Claude 輸入 `Analyze the current screen and write one Maestro flow per tab in the bottom tab bar` | 介紹 `analyze_screen` 抓 `maestro hierarchy`，分 form / cta / tab_bar |
| 1:55 | 顯示產出多個 `.yaml` flows | 「一個 tab 一個 flow，全部 runnable Maestro YAML。」 |
| 2:05 | 跑其中一個 → 模擬器畫面真的動起來 | 「真機、模擬器、BlueStacks 走遠端 ADB 都吃。」 |

### 2:15–2:45 自我改善層（殺手鐧，沒人有）

| 時間 | 操作 | 旁白要點 |
|---|---|---|
| 2:15 | 跑 `get_test_history` 顯示過去 N 次 run | 「跑了一週後，告訴它：分析我的 suite。」 |
| 2:25 | 跑 `get_optimization_plan` → 畫面顯示 `optimization-plan.md` 開啟 | 「它輸出三層改善建議：suite 健康度、MCP 工具用得對不對、AI prompt 策略。」 |
| 2:35 | 切到 markdown，scroll 顯示 flaky 列表、覆蓋率盲點、建議 | 「這不是 dashboard，這是教練。」 |

### 2:45–3:00 Outro

- 字卡：「**One MCP. 5 runners. Web + mobile. Analyze → generate → run → coach.**」
- 顯示三個 CTA：
  - `smithery.ai/server/kao273183/mk-qa-master`
  - `github.com/kao273183/mk-qa-master`
  - `pip install mk-qa-master` / `uvx mk-qa-master`
- 旁白：「Open source, MIT. Star it on GitHub or one-click install from Smithery.」

---

## 錄製清單（避免重錄）

- [ ] 受測站打開可登入帳號（`tomsmith` / `SuperSecretPassword!`）
- [ ] `tests_project/` 已清空舊產物
- [ ] Claude Desktop config 已指到正確 `QA_PROJECT_ROOT`
- [ ] Maestro 段：模擬器 booted、有一支可分析的 app（YouTube / 任何裝好的 app 即可）
- [ ] 字卡素材：5 個 runner 的 logo PNG（free 來源：simpleicons.org）
- [ ] BGM：lo-fi、無人聲、音量 -18dB 以下（Pixabay Music 有免費）
- [ ] 結尾卡的兩個 URL 確定還活著

## 剪輯重點

- **每 8 秒至少一個視覺變化**（cut、zoom、字卡），不然滑掉
- 旁白語速可以略快——觀眾看 demo 是想看「動」的，不是聽你解釋
- Terminal 跑指令時用 2x 或 3x 速播放，*除了* 最關鍵的那個（`run_tests` pass 那一刻保留實速）
- 字卡用同一個顏色系統（主色 + 一個對比色），不要五彩繽紛

## 投放優先序

1. **YouTube**：完整版上傳，做縮圖（左：螢幕截圖、右：「One MCP. 5 runners.」大字）
2. **Smithery listing**：嵌入 60 秒版
3. **README**：頂部放 GIF 或 YouTube 連結
4. **X / LinkedIn / Reddit r/mcp / r/ClaudeAI**：60 秒版 + 上面的貼文模板
5. **iThome 鐵人賽 / 掘金 / 少數派**：把完整版嵌入長文當主視覺
