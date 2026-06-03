"""Read project-level QA knowledge so the MCP can escape monkey-testing.

Two-layer model:
  - Universal QA methodology (ISTQB principles, BBT design techniques, test
    pyramid, regression strategy, mobile checklist, QA metrics, API testing
    methodology, flakiness taxonomy, test doubles, test data management) is
    bundled as the built-in fallback. Applicable to any testing project.
  - Domain knowledge (business rules, regression points, exact assertion
    strings, user journeys, infra constraints) is per-project — users
    create qa-knowledge.md (via init_qa_knowledge or manually).

When the project file exists, we return *only* the user's content (their
file is the source of truth). When it doesn't, we return the built-in
methodology + TODO placeholders pointing at domain sections to fill.

Convention: the file uses H2 headers (## Section name) to delimit topics.
The client can fetch a single section by name (case-insensitive, partial
match) when it needs just one slice.

Bilingual since v0.6.2: the built-in methodology ships in English
(default, QA_LANG=en) and Traditional Chinese (QA_LANG=zh-tw). Each
language carries the same 13 H2 sections. Adapted translation, not
literal — code blocks / tool names / file paths are not translated.
The user's own qa-knowledge.md is served back unchanged regardless of
QA_LANG; only the built-in fallback and the starter template body
respond to the language toggle.
"""
import re

from ..config import QA_KNOWLEDGE_PATH, QA_LANG


# ---------------------------------------------------------------------------
# Traditional Chinese methodology (the original v0.6.1 content, verbatim).
# Hand-written; do NOT re-translate from English. New 4 sections at the end
# mirror the English-side additions for parity.
# ---------------------------------------------------------------------------
_UNIVERSAL_METHODOLOGY_ZH = """## ISTQB 七大測試原則
| # | 原則 | 說明 | 實際應用 |
|---|------|------|---------|
| 1 | **測試證明缺陷的存在** | 測試能發現 Bug，但不能證明沒有 Bug | 不要因為測試通過就假設零缺陷 |
| 2 | **窮盡測試不可能** | 不可能測試所有組合 | 用風險和優先度聚焦，不追求 100% 覆蓋 |
| 3 | **早期測試** | 越早測試，修復成本越低 | Shift-Left：需求階段就開始審查 |
| 4 | **缺陷聚集** | 80% 的 Bug 集中在 20% 的模組 | 高風險模組優先測試（登入、支付、SSO） |
| 5 | **殺蟲劑悖論** | 同樣的測試反覆跑，找不到新 Bug | 定期更新測試用例 + 探索性測試 |
| 6 | **測試依賴上下文** | 不同系統需要不同測試策略 | 電商 App vs 社交 App 的測試重點不同 |
| 7 | **「零缺陷」謬誤** | 沒有 Bug 不代表系統符合需求 | 功能正確 ≠ 使用者滿意，還需可用性測試 |

## 測試設計技術 — 黑箱
### 等價分割 (Equivalence Partitioning)
- 將輸入分成等價類，每類取一個代表值
- 同一類中的值，系統行為相同
- 範例：訂購數量 1-10
  - 無效分區：≤ 0（測試 0）
  - 有效分區：1-10（測試 5）
  - 無效分區：≥ 11（測試 11）

### 邊界值分析 (Boundary Value Analysis)
- Bug 最容易發生在邊界上
- 測試點：最小值、最小+1、中間值、最大-1、最大值
- 範例：數量 1-10 → 測試 0, 1, 2, 5, 9, 10, 11
- 與等價分割搭配：EP 選分區代表，BVA 選邊界

### 決策表測試 (Decision Table Testing)
- 適用：多條件組合影響不同結果
- 建構：n 個條件 → 2^n 種組合
- 範例：登入畫面（帳號正確 × 密碼正確）

  | 帳號 | 密碼 | 結果 |
  |------|------|------|
  | ✗ | ✗ | 錯誤 |
  | ✓ | ✗ | 錯誤 |
  | ✗ | ✓ | 錯誤 |
  | ✓ | ✓ | 登入成功 |

### 狀態轉換測試 (State Transition Testing)
- 適用：系統行為依賴歷史狀態
- 四要素：狀態、轉換、事件、動作
- 範例：ATM 密碼（3 次機會）

  | 狀態 | 正確 PIN | 錯誤 PIN |
  |------|---------|---------|
  | 初始 | 進入 | 第 2 次 |
  | 第 2 次 | 進入 | 第 3 次 |
  | 第 3 次 | 進入 | 鎖定 |

## 測試金字塔 (Google 70/20/10)
```
        /‾‾‾‾‾‾\\
       / E2E 10% \\      少量、慢、脆弱、昂貴
      /────────────\\
     / Integration  \\    中量、驗證模組互動
    /   20%          \\
   /──────────────────\\
  /   Unit Tests 70%   \\  大量、快、穩定、便宜
 /────────────────────────\\
```

### 反模式
- **冰淇淋甜筒**（倒金字塔）：大量 E2E、少量 Unit → 慢、脆弱
- **沙漏型**：多 Unit + 多 E2E、少 Integration → 缺少模組間驗證

### 各層重點
| 層 | 速度 | 穩定性 | 測什麼 | 工具範例 |
|----|------|--------|--------|---------|
| Unit | 毫秒 | 高 | 單一邏輯、邊界、錯誤處理 | pytest / XCTest / JUnit |
| Integration | 秒 | 中 | 模組間互動、API、DB | requests-mock / URLProtocol |
| E2E | 分鐘 | 低 | 關鍵業務流程 | Playwright / Cypress / XCUITest |

### 關鍵原則
- 高層測試發現 Bug → 必須補一個低層測試
- 盡量將測試往金字塔底層推
- E2E 只測「能賺錢或會賠錢」的流程

## Shift-Left 測試
### 定義
將測試活動提前到開發生命週期早期，而非等開發完成後才測試。

### 四種類型
| 類型 | 說明 |
|------|------|
| 傳統型 | V-Model，強調 Unit + Integration |
| 增量型 | 分模組逐步測試 |
| Agile/DevOps 型 | 每個 Sprint 持續測試 |
| 基於模型型 | 需求階段就抓 Bug |

### 實踐方法
1. **靜態測試**：需求審查、設計審查（Code Review 也是）
2. **開發者自測**：寫完程式碼立即跑測試
3. **統一工具鏈**：開發和測試用同一套工具
4. **自動化 CI**：每次 commit 觸發測試
5. **持續回饋**：測試結果立即回饋給開發

### 成本效益
- 需求階段修 Bug：$1
- 開發階段修 Bug：$10
- 測試階段修 Bug：$100
- 上線後修 Bug：$1000

## 回歸測試策略
### 何時執行
- 新功能整合後
- Bug 修復後
- 需求變更後
- 效能優化後
- 外部系統整合後

### 測試用例選擇（優先度）
1. 歷史上缺陷頻發的功能
2. 使用者可見的功能
3. 核心業務功能
4. 最近修改的區域
5. 所有整合測試
6. 複雜和邊界值測試

### 類型
| 類型 | 範圍 | 適用 |
|------|------|------|
| Unit | 只測修改部分 | 小改動 |
| Regional | 修改 + 依賴模組 | 模組改動 |
| Full | 全部重測 | 大改版 / Release |
| Selective | 影響範圍子集 | 時間有限 |

### 最佳實踐
- 盡可能自動化
- 整合到 CI/CD pipeline
- 每次 code change 後執行
- 維持一致的測試環境
- 使用隔離、可重現的測試資料

## Mobile App 測試 Checklist
### 功能測試
- [ ] 必填欄位有明顯區分
- [ ] App 啟動/停止正常
- [ ] 來電時 App 行為正確
- [ ] 收 SMS 時不影響 App
- [ ] 多工切換正常
- [ ] 社群分享功能正常
- [ ] 支付閘道正常（Visa/MC/Apple Pay）
- [ ] 網路失敗有適當錯誤訊息
- [ ] 系統 crash 後 App 能恢復
- [ ] 安裝/更新流程無重大錯誤

### 效能測試
- [ ] 不同負載下回應時間可接受
- [ ] 峰值使用者數量下網路覆蓋充足
- [ ] 電池續航在預期負載下足夠
- [ ] WiFi ↔ 4G/5G 切換不影響功能
- [ ] CPU 使用率優化
- [ ] 記憶體洩漏檢測
- [ ] GPS/Camera 等資源使用合理
- [ ] 長時間使用穩定性

### 安全測試
- [ ] 暴力破解防護
- [ ] 敏感內容需認證
- [ ] 強密碼策略
- [ ] Session 過期時間合理
- [ ] SQL Injection 防護
- [ ] SSL/TLS 憑證驗證
- [ ] 加密存儲（Keychain / Encrypted SharedPreferences）
- [ ] 鍵盤快取安全
- [ ] Cookie 安全

### 可用性測試
- [ ] 按鈕大小適合觸控（最小 44pt）
- [ ] 按鈕位置一致不混淆
- [ ] 圖示直覺一致
- [ ] 相同功能顏色一致
- [ ] 有縮放功能
- [ ] 盡量減少鍵盤輸入
- [ ] 有返回/取消功能
- [ ] 文字大小可讀
- [ ] 大量下載有提示
- [ ] 關閉 App 後狀態保留

### 相容性 / 中斷 / 恢復
- [ ] 不同螢幕尺寸 UI 適應
- [ ] 文字不被截斷
- [ ] 不同 OS 版本可運行
- [ ] 網路中斷後鍵盤輸入不中斷
- [ ] 充電時背景 App 效能正常
- [ ] 低電量 + 高負載組合
- [ ] Crash 後資料完整性
- [ ] 連線中斷後資料恢復
- [ ] 解除安裝後無殘留檔案

## RWD 響應式測試（Web）
### 標準 breakpoints（常用、依專案 design system 調整）
| 區段 | 寬度 (px) | 代表裝置 |
|------|----------|---------|
| Mobile XS | 320–374 | iPhone SE 1st gen / 早期 Android |
| Mobile | 375–413 | iPhone SE 3rd / iPhone 13–16 mini |
| Mobile L | 414–767 | iPhone Pro Max / Plus 系列 |
| Tablet | 768–1023 | iPad 直向 |
| Tablet L | 1024–1279 | iPad 橫向 / iPad Pro |
| Desktop | 1280–1919 | 標準 laptop / 外接螢幕 |
| Desktop XL | ≥ 1920 | 大型外接螢幕 / 4K |

### 必測軸
- **排版不破**：text 不溢出 / 不截斷 / layout 不重疊
- **互動切換**：mobile 用 hamburger + tap；desktop 用 hover + mouse；過渡區（tablet）兩者皆要可用
- **觸控目標**：mobile 點擊區 ≥ 44×44pt（WCAG 2.5.5 / Apple HIG）
- **媒體切換**：`<picture srcset>` / `image-set()` 在不同 DPR 載對解析度
- **字體可讀**：base font-size ≥ 14px；行高 1.4–1.6；尺寸用 rem/em（隨用戶縮放）
- **鍵盤導航**：tab order 不因 RWD 重排而錯亂

### 常見 RWD bug 模式（每條都該寫 TC）
- **px 寫死**：用戶字體放大或縮放時 layout 爆掉
- **Hover only 互動**：mobile 無 hover、卡在 hover 後狀態不會觸發
- **隱藏不卸載**：`display:none` 但 DOM 還在 → selector 抓到不可見元素誤判
- **viewport meta 缺失**：手機看像縮小桌面版、不會 reflow
- **過渡區未測試**：768–1023 排版錯位（mobile / desktop 都正常、tablet 破）
- **Image 不 lazy / srcset 缺失**：mobile 下載桌面大圖、流量爆 + 載入慢

### 測試策略
- **E2E**：核心流程選 3 個代表 viewport（mobile 375 / tablet 768 / desktop 1280）各跑一次
- **視覺回歸**：用 visual diff 工具（Percy / Chromatic）跨 viewport 比對
- **邊界值思維**：breakpoint ± 1px（如 767 vs 768）應一致或明確切換，不能曖昧
- **真機優先**：CSS emulator ≠ 真實 mobile（iOS Safari bottom bar / 安卓 IME 高度 / 動態島）

## 測試類型總覽
| 類型 | 定義 | 自動化 | 頻率 |
|------|------|--------|------|
| **冒煙測試** | 建構後快速驗證核心功能 | 應自動化 | 每次 Build |
| **回歸測試** | 確認改動沒破壞既有功能 | 應自動化 | 每次 Change |
| **功能測試** | 驗證業務需求 | 部分自動化 | 每次 Feature |
| **整合測試** | 驗證模組間互動 | 應自動化 | 每次整合 |
| **E2E 測試** | 完整使用者旅程 | 關鍵路徑自動化 | Release 前 |
| **效能測試** | 負載、回應時間、資源 | 工具輔助 | 定期 |
| **安全測試** | 漏洞、認證、加密 | 部分工具 | Release 前 |
| **探索性測試** | 無腳本、經驗導向 | 手動 | 新功能 |
| **驗收測試** | 符合業務標準 | 部分 | Release 前 |

## QA Metrics
| 指標 | 公式 | 目標 |
|------|------|------|
| **執行率** | (Pass + Fail) / Total | > 95% |
| **通過率** | Pass / (Pass + Fail) | > 95% |
| **缺陷密度** | Bug 數 / KLOC | 越低越好 |
| **缺陷移除效率** | 上線前 Bug / 總 Bug | > 90% |
| **回歸通過率** | 回歸 Pass / 回歸 Total | > 98% |
| **自動化覆蓋** | 自動化 TC / 總 TC | 依層級 |
| **平均修復時間** | 修復完成 - Bug 提交 | 越短越好 |

## API 測試方法論
### Schema-driven 測試
- 以 OpenAPI 3.x / Swagger 2.0 / JSON Schema 作為單一事實來源
- 自動生成請求參數、邊界值、錯誤路徑、回應結構驗證
- 工具：Schemathesis（HTTP 端到端、property-based）、Dredd（合約 smoke）
- 上手最快：把現有 spec 餵給 Schemathesis 直接跑 — 等同自動化的「ISTQB 邊界值 + 等價分割」

### 合約測試 (Consumer-Driven Contracts)
- **Pact** 是業界事實標準：consumer 寫期望、broker 儲存、provider 驗證
- 解決微服務「我這邊測了 OK、整合時掛掉」的鴻溝
- 流程：consumer 跑單元測試 → 產出 pact JSON → 推到 broker → provider CI 拉下來重播驗證
- 與 schema 測試的分工：schema 驗形狀；contract 驗「兩個服務對形狀的認知一致」

### Property-based 測試
- 不寫固定範例，描述「對所有合法輸入都應成立的性質」
- 工具：Python `hypothesis`、JS `fast-check`、HTTP 層 `schemathesis`
- 範例：對所有合法的 `POST /orders` payload，回應一定有 `id` 且為 UUID
- 找出邊界 bug 的效率遠高於人工窮舉

### 認證模式 (Auth Patterns)
| 模式 | 怎麼測 |
|------|--------|
| Bearer Token | `Authorization: Bearer <jwt>`；測過期、簽章錯、缺 scope |
| OAuth2 client_credentials | 先 POST `/oauth/token`、再帶 access_token；測 token 過期自動刷新 |
| API Key Header | `X-API-Key: <key>`；測 quota 用罄、IP 白名單 |
| mTLS | 雙向 cert；測 cert 過期、CN 不匹配 |

### 冪等性 Key (Idempotency Keys)
- 客戶端帶 `Idempotency-Key: <uuid>`，伺服器在 N 分鐘內對同 key 回相同結果
- 必測：同 key 重送回 200 + 相同 body；不同 key 視為新請求
- 適用：金流、`POST /payments`、外部 webhook 重送

### 速率限制處理 (Rate Limit Handling)
- 標準：HTTP 429 + `Retry-After: <seconds>` header
- 客戶端策略：指數退避（initial 1s、cap 60s、jitter ±20%）
- 測試重點：連打觸發 429、`Retry-After` 後重試成功、超過 max retries 後正確失敗

### 分頁策略 (Pagination)
| 策略 | 適用 | 痛點 |
|------|------|------|
| Offset/Limit | 小資料、UI 顯示頁碼 | 大 offset 慢、資料變動時跳項 |
| Cursor | 串流 / feed | 不能跳頁、cursor 失效要處理 |
| Keyset | 大表時間序 | 需穩定排序鍵、不支援任意排序 |

### 錯誤回應格式 (Error Shape)
- **RFC 7807 problem+json**：`{ type, title, status, detail, instance }` — REST 業界共識
- **GraphQL `errors[]`**：每個 error 有 `message` / `path` / `extensions.code`
- 必測：錯誤路徑回應符合自己訂的形狀（不只 status code 對就算 pass）
- 反模式：200 OK 內含 `{ "error": "..." }` — 監控、retry、log 全失靈

## Flaky 測試根因分類
> 大多數「flaky」測試失敗其實不是 flake — 是有根因可循的。Optimizer 的
> `broken` 分類用在連續三次失敗共用同一錯誤簽章；那是真 bug，不是 flake。
> 真正 flake 多半落在以下五類：

### 1. 競態條件 (Race Conditions)
- **Smell**: 同樣的測試重跑會通過、loop 100 次有 1-2 次掛
- **Fix**: 用顯式 wait（`wait_for_response` / `expect(locator).to_be_visible`），不要用 `sleep(N)`
- **Example**: 點擊按鈕觸發 AJAX，後面 assert 文字之前沒等回應 → 偶爾在 response 前 assert

### 2. 外部依賴 (External Dependencies)
- **Smell**: CI 跑會掛、本機跑不會；網路高峰時段失敗率高
- **Fix**: 對 third-party 用 mock / VCR / contract test；時間用 freeze；網路用 retry-with-backoff
- **Example**: 整合測試打真實 Stripe sandbox，sandbox 暫時不穩 → 測試紅了但程式沒事

### 3. 順序相依 (Order-Dependent)
- **Smell**: 單跑某 test 通過、整批跑會掛；換個 -p random seed 結果不同
- **Fix**: 每個 test 自帶 setup/teardown；不共用 module-level 變數或 DB rows
- **Example**: test_a 寫了 user record、test_b 假設 DB 空 → test_b 在 test_a 之後跑就掛

### 4. 時間敏感 (Time-Sensitive)
- **Smell**: 半夜跑會掛、跨日 / 跨時區會掛、DST 切換週末出問題
- **Fix**: 用 freezegun / jest fake timers 固定時鐘；別用 `datetime.now()` 直接比對
- **Example**: 訂單建立時 `created_at` 使用 server time，斷言寫「今天」字串 → 跨 0 點的測試掛掉

### 5. 資源洩漏 (Resource Leaks)
- **Smell**: 測試套件越跑越慢、最後幾個 test 掛在 timeout、CI 機 OOM
- **Fix**: fixture 用 yield/teardown 確保關 socket / file / process / page
- **Example**: Playwright 測試沒關 context → 累積 50 個 chromium process 後 CI runner 爆記憶體

## 測試替身 (Test Doubles — Mock / Stub / Fake / Spy)
> 採用 Martin Fowler 的標準四分類。四者目的不同、適用場景不同 —
> 不要把它們混為一談、不要全部叫 "mock"。

### Stub — 回傳預設資料
- **特性**: 提供固定回應，**不驗證如何被呼叫**
- **用在**: 隔離外部依賴、聚焦在 SUT 行為（state verification）
- **範例**: stub 一個 `get_exchange_rate()` 永遠回 30.0，測試「換匯計算邏輯」

### Mock — 預設資料 + 驗證呼叫方式
- **特性**: 回固定資料 + 斷言「該方法被呼叫幾次、用什麼參數」（behavior verification）
- **用在**: 驗證互動契約，例如「下單後一定要呼叫 audit_log.write()」
- **範例**: `mock_audit.assert_called_once_with(order_id=42)` — 行為斷言

### Fake — 簡化但可運作的實作
- **特性**: 真有邏輯、能跑，但比 production 簡單（記憶體 vs DB；本地 vs 雲端）
- **用在**: 整合測試需要可運作的依賴又不想拉真服務
- **範例**: SQLite in-memory 取代 Postgres；本地 fakeredis 取代 ElastiCache

### Spy — 包真實物件並記錄呼叫
- **特性**: 物件**真的執行**，spy 在旁邊記下呼叫次數 / 參數，事後檢查
- **用在**: 需要真行為 + 觀察用，例如「真的有發 email、且只發了一次」
- **範例**: spy 包住 `EmailService.send`，斷言 `spy.call_count == 1`

### 反模式
- **Over-mocking**：每個依賴都 mock → 測試斷言的是 mock 的行為、不是 production 的行為
- **Mock what you don't own**：mock 第三方 SDK 內部細節 → SDK 升版後測試還是綠的、production 卻爆
- **Mock everything then test nothing**：層層 mock 後 SUT 的真實邏輯沒測到，覆蓋率高但毫無保護力
- **Roo決**：state verification 優先（stub + fake），behavior verification 只在「互動本身就是需求」時用（mock）

## 測試資料管理
### Factories
- `factory_boy`（Python）、`faker`（多語言）：宣告式生資料、預設亂數 + 可覆寫
- 比 fixture 檔案彈性：每個 test 微調 `OrderFactory(amount=999)`，其他欄位自動填合理值
- 適合：物件構造路徑複雜、欄位多

### Fixtures
- `pytest.fixture` / Jest `beforeEach`：可重用 setup，scope 控制（function / class / session）
- 適合：每 test 都需相同 setup 的小型靜態資料

### 隔離策略
- **Per-test transaction rollback**：DB 測試在交易內跑、結束時 rollback → 不留垃圾
- **Per-test temp dir**：檔案系統測試用 `tmp_path`；測試結束自動清
- **Per-test namespace**：containers / k8s 整合測試每 case 一個 namespace

### 時間 mocking
- `freezegun`（Python）：`with freeze_time("2026-05-16"):` 鎖定 `datetime.now()`
- Jest fake timers：`jest.useFakeTimers()` 鎖 `Date.now()` + `setTimeout`
- iOS XCTest 用 dependency injection 把 `Clock` 抽象化、測試注入假 clock

### 資料庫 seeding
- **Per-suite seed**：跑前一次性灌、所有 test 共用 → 快但不隔離（容易順序相依）
- **Per-test seed**：每 test 自己 setup → 慢但乾淨（首選）
- 折衷：基礎參考資料 per-suite（國家 / 商品分類 / 角色），交易資料 per-test

### Fresh data vs 共用 fixture 的取捨
| 共用 fixture（per-suite） | 新鮮資料（per-test） |
|---|---|
| 跑得快 | 隔離乾淨 |
| 容易順序相依 | 不會互相污染 |
| 適合大、慢的 setup | 適合主要業務 case |
| 適合：基準參考表 | 適合：交易、變更類 case |

## 驗證碼 (CAPTCHA) 測試策略

驗證碼 (CAPTCHA / reCAPTCHA / hCaptcha / Cloudflare Turnstile) 是自動化測試最常卡住的點。**90% 的場景都不該「解」驗證碼、該繞過**。

### Tier 1：Bypass — 第一首選

| 方法 | 怎麼做 | 適用情境 |
|---|---|---|
| **reCAPTCHA test keys** | Staging 換 Google 官方測試 key (site: `6LeIxAcTAAAAAJcZVRqyHh71UMIEGNQ_MXjiZKhI` / secret: `6LeIxAcTAAAAAGG-vFI1TnRWxMZNFuojJ4WifJWe`) — 任何驗證自動 pass | 自家站、有 backend 控制權 |
| **Feature flag** | Backend 加 `DISABLE_CAPTCHA_IN_STAGING=true`，staging build 直接跳過 | 自家站 |
| **Test-mode header** | QA 跑測試帶 `X-Test-Mode: <secret>`，backend 驗到就 bypass | 自家站 |
| **IP allowlist** | QA runner IP 走 allowlist 不過 CAPTCHA | 有固定 QA infra |

**最推 reCAPTCHA test keys** — 零 backend code、Google 官方背書、100% 成功率、零成本。

### Tier 2：Degrade gracefully — 沒 backend 控制權時

- 偵測到 CAPTCHA iframe -> 截圖、標記 test 為 `external_dependency`、跳過後續斷言
- mk-qa-master 的 optimizer 看到連續因 CAPTCHA 失敗 -> 分類為 `external` 而非 `broken` / `flaky`
- CI 階段 skip CAPTCHA 後路徑，local dev 走 Tier 3 手動或 AI 視覺判斷

### Tier 3：AI 視覺判斷 (v0.7.0+) — 最後手段

mk-qa-master 計劃中的 `solve_visual_challenge` tool：

1. 偵測 CAPTCHA iframe (reCAPTCHA v2 / hCaptcha 圖片格)
2. 截圖 challenge + 抓 tile selectors
3. 把截圖回傳給 AI client (Claude / Cursor — 自帶 vision)
4. AI 看圖回「點 [0, 4, 7] 號 tile」
5. Runner 執行 click 鏈、提交、繼續

**限制：**

- 對 reCAPTCHA v2 / hCaptcha 圖片題可行，成功率 60-80%
- 對 reCAPTCHA v3 / Cloudflare Turnstile **無題目可看** (純行為打分)，只能靠 stealth plugin + 真實滑鼠軌跡 + IP 信譽
- Google 偵測到自動化會 ban session/IP，**不可在 production 跑**
- 法律：**自己的站或客戶授權站才能用**，第三方站涉及 TOS 違反

### 決策流程

```
你的站、有 backend 權限   -> Tier 1 (test keys)
你的站、沒法改 backend    -> 走代理或 IP allowlist (Tier 1 變體)
客戶站、有授權            -> Tier 1 -> fallback Tier 3
不是你的站                -> 不要跑 CAPTCHA bypass / solver
```

## 邊緣視覺推論測試 (v1.1.0+)

`QA_RUNNER=edge` 把 RTSP 串流 + YOLO 推論 + pytest 斷言串成單一測試流。本段歸納跑 Edge AI 測試時與一般 web/mobile 測試最不一樣的幾條領域原則。

### 核心原則

- **偵測正確性一律用 IoU 門檻、禁止精確座標比對**。AI 輸出本質模糊，pixel-level 比對只是在驗 AI 的隨機性，不是驗業務正確性。`mk_qa_master.edge.metrics.match_detection` 走 IoU，預設門檻 0.5。
- **效能與正確性同等重要**。慢半拍的正確答案在產線上等於失效。每個 detection 測試應該成對地寫 throughput / latency 斷言，p95 latency 比 mean latency 更能反映實況。
- **每個輸入必須能回溯到幀號**。把幀號燒入影片角落或讀 `cv2.CAP_PROP_POS_FRAMES`，否則 fail 報告只能說「某個時間點 detection 沒中」，無法復現。
- **韌性情境是一級公民**。斷流重連、壞幀、非預期 codec 都不能讓測試 crash。建議至少有：(1) 中途 `kill ffmpeg` 模擬斷流；(2) 故意餵壞 GOP；(3) 用 `tc qdisc netem` 注入網路抖動。
- **空畫面 / 無目標幀的誤報率單獨追蹤**。一個健康的模型不該對純色畫面或 noise 產生 detection。每個 suite 至少有一條 `test_empty_frame_no_false_positives`。

### Edge runner 特有風險

- **mediamtx + ffmpeg 啟動順序敏感**：runner 已用 socket 就緒探測規避，但 CI 上偶爾仍會 race。若看到「open RTSP 失敗」先檢查 `start_rtsp_source` 的 timeout 是否被縮短。
- **YOLO 模型載入只能 session-scope**：`backend` fixture 是 session-scoped 不是 function-scoped。誤改成 function-scoped 會讓每個 test case 都重載模型、suite 跑爆。
- **QA_JETSON_HOST / QA_INFERENCE_ENDPOINT 沒設時走 LocalYolo**：v1.1 不支援這兩條，呼叫 `.infer()` 會丟 NotImplementedError。Phase 3 (v1.2) 補。
- **Vendor-host blacklist 預設擋 Dahua / Hikvision 等廠牌**：自己擁有的攝影機才設 `QA_EDGE_ALLOW_VENDOR_HOSTS=true`，不然測試會收到 `forbidden_vendor_host` envelope。

### 推薦 SLA 預設

| 場景 | min_fps | latency_sla_ms | iou_threshold |
|---|---|---|---|
| 桌機 yolov8n 開發 | 25 | 40 | 0.5 |
| Jetson Nano | 15 | 70 | 0.5 |
| Jetson Orin Nano | 30 | 25 | 0.6 |
| 雲端 GPU 推論服務 | 60 | 16 | 0.6 |

行數字當起點調，超出 SLA 第一次就 fail、不要等多次跌破才補測。
"""


# ---------------------------------------------------------------------------
# English methodology (NEW in v0.6.2 — default served when QA_LANG=en).
# Adapted translation: idiomatic English, paragraphs reflowed for natural
# reading. Code blocks / tool names / file paths kept verbatim. Same 13
# H2 sections as the zh-TW version so the section-name lookup works
# across languages, modulo translated section titles.
# ---------------------------------------------------------------------------
_UNIVERSAL_METHODOLOGY_EN = """## ISTQB Seven Testing Principles
| # | Principle | What it means | How to apply it |
|---|------|------|---------|
| 1 | **Testing shows defects, not their absence** | Tests can find bugs but cannot prove a system is bug-free | Don't assume zero defects just because the suite is green |
| 2 | **Exhaustive testing is impossible** | You cannot test every combination | Prioritize by risk and impact; stop chasing 100% coverage |
| 3 | **Early testing saves money** | The earlier you test, the cheaper the fix | Shift-Left: review requirements before code is written |
| 4 | **Defect clustering** | 80% of bugs live in 20% of modules | Test the high-risk modules first (login, payments, SSO) |
| 5 | **Pesticide paradox** | The same tests stop finding new bugs over time | Refresh test cases regularly; add exploratory sessions |
| 6 | **Testing is context-dependent** | Different systems need different strategies | An e-commerce app and a social app have different test priorities |
| 7 | **Absence-of-errors fallacy** | No bugs ≠ system meets user needs | Correctness ≠ usability — still test the user experience |

## Test Design Techniques — Black-Box
### Equivalence Partitioning
- Divide inputs into classes where the system behaves identically; test one representative per class
- Example: order quantity 1–10
  - Invalid partition: <= 0 (test 0)
  - Valid partition: 1–10 (test 5)
  - Invalid partition: >= 11 (test 11)

### Boundary Value Analysis
- Bugs cluster at boundaries
- Test points: min, min+1, middle, max-1, max
- Example: quantity 1–10 -> test 0, 1, 2, 5, 9, 10, 11
- Pair with EP: EP picks partition reps; BVA picks the edges

### Decision Table Testing
- Best for: multiple conditions combining into different outcomes
- Build: n conditions -> 2^n combinations
- Example: login (account valid x password valid)

  | Account | Password | Result |
  |------|------|------|
  | X | X | Error |
  | OK | X | Error |
  | X | OK | Error |
  | OK | OK | Success |

### State Transition Testing
- Best for: behavior that depends on prior state
- Four parts: state, transition, event, action
- Example: ATM PIN (3 attempts allowed)

  | State | Correct PIN | Wrong PIN |
  |------|---------|---------|
  | Initial | Enter | Attempt 2 |
  | Attempt 2 | Enter | Attempt 3 |
  | Attempt 3 | Enter | Locked |

## Test Pyramid (Google 70/20/10)
```
        /‾‾‾‾‾‾\\
       / E2E 10% \\      few, slow, brittle, expensive
      /────────────\\
     / Integration  \\    moderate, verify module interactions
    /   20%          \\
   /──────────────────\\
  /   Unit Tests 70%   \\  many, fast, stable, cheap
 /────────────────────────\\
```

### Anti-patterns
- **Ice-cream cone** (inverted pyramid): heavy E2E, light unit -> slow, brittle
- **Hourglass**: many unit + many E2E, few integration -> module interactions not verified

### Layer focus
| Layer | Speed | Stability | What to test | Tooling |
|----|------|--------|--------|---------|
| Unit | ms | high | single logic, edges, error handling | pytest / XCTest / JUnit |
| Integration | s | medium | module interactions, API, DB | requests-mock / URLProtocol |
| E2E | min | low | critical business flows | Playwright / Cypress / XCUITest |

### Key principles
- When a high-layer test catches a bug, add a lower-layer test that would have caught it sooner
- Push tests down the pyramid whenever possible
- Only run E2E against flows that "make money or cost money"

## Shift-Left Testing
### What it is
Move testing activities earlier in the development lifecycle instead of waiting until after code is "done."

### Four flavors
| Flavor | Description |
|------|------|
| Traditional | V-Model with emphasis on Unit + Integration |
| Incremental | Module-by-module verification |
| Agile/DevOps | Continuous testing every sprint |
| Model-based | Catch bugs during requirements modeling |

### Practices
1. **Static testing**: requirements review, design review (code review counts)
2. **Developer self-test**: run tests the moment code is written
3. **Unified toolchain**: dev and QA share the same tools
4. **CI automation**: every commit triggers tests
5. **Continuous feedback**: failures surface to developers immediately

### Cost-of-defect curve
- Fix during requirements: $1
- Fix during development: $10
- Fix during QA: $100
- Fix in production: $1000

## Regression Test Strategy
### When to run
- After integrating a new feature
- After fixing a bug
- After a requirement change
- After a performance optimization
- After integrating an external system

### Test selection priority
1. Features with a history of recurring defects
2. User-visible features
3. Core business features
4. Recently modified areas
5. All integration tests
6. Complex paths and boundary-value cases

### Types
| Type | Scope | When to use |
|------|------|------|
| Unit | Just the modified piece | Tiny changes |
| Regional | Modified piece + dependencies | Module-level change |
| Full | Re-test everything | Major versions / releases |
| Selective | Subset by impact analysis | Time-constrained |

### Best practices
- Automate as much as possible
- Wire into the CI/CD pipeline
- Run on every code change
- Keep test environments consistent
- Use isolated, reproducible test data

## Mobile App Test Checklist
### Functional
- [ ] Required fields are visually distinguishable
- [ ] App start/stop behaves correctly
- [ ] Incoming calls don't break app state
- [ ] SMS arrival doesn't interrupt the app
- [ ] Multitasking switching is clean
- [ ] Social sharing works
- [ ] Payment gateways pass (Visa/MC/Apple Pay)
- [ ] Network failures show actionable error messages
- [ ] App recovers gracefully from system crash
- [ ] Install/update flow has no blocking errors

### Performance
- [ ] Response time stays acceptable under varied load
- [ ] Network capacity holds at peak user count
- [ ] Battery life is acceptable under expected load
- [ ] WiFi <-> 4G/5G switching doesn't break flows
- [ ] CPU usage is reasonable
- [ ] No memory leaks
- [ ] GPS / camera / sensor use is bounded
- [ ] Long-session stability holds

### Security
- [ ] Brute-force protection in place
- [ ] Sensitive content requires authentication
- [ ] Strong password policy enforced
- [ ] Session expiry is reasonable
- [ ] SQL injection defenses verified
- [ ] SSL/TLS certificate validation enforced
- [ ] Encrypted storage (Keychain / Encrypted SharedPreferences)
- [ ] Keyboard cache cleared for sensitive fields
- [ ] Cookies set with secure flags

### Usability
- [ ] Touch targets at least 44pt
- [ ] Button placement is consistent across screens
- [ ] Icons are intuitive and consistent
- [ ] Same function uses the same color across screens
- [ ] Zoom / pinch behavior available
- [ ] Minimal keyboard input required
- [ ] Back / cancel always reachable
- [ ] Text is legible at default size
- [ ] Large downloads show progress
- [ ] App state survives backgrounding

### Compatibility / Interrupt / Recovery
- [ ] UI adapts across screen sizes
- [ ] Text never truncated unintentionally
- [ ] Runs on supported OS versions
- [ ] Keyboard input survives network disruption
- [ ] Background app performance OK during charging
- [ ] Low battery + high load combo handled
- [ ] Data integrity preserved after crash
- [ ] Data recovers after connection drops
- [ ] Uninstall leaves no orphan files

## RWD Responsive Testing (Web)
### Standard breakpoints (typical; adjust to your design system)
| Tier | Width (px) | Representative devices |
|------|----------|---------|
| Mobile XS | 320–374 | iPhone SE 1st gen / early Android |
| Mobile | 375–413 | iPhone SE 3rd / iPhone 13–16 mini |
| Mobile L | 414–767 | iPhone Pro Max / Plus |
| Tablet | 768–1023 | iPad portrait |
| Tablet L | 1024–1279 | iPad landscape / iPad Pro |
| Desktop | 1280–1919 | Standard laptop / external monitor |
| Desktop XL | >= 1920 | Large monitor / 4K |

### Must-test axes
- **Layout doesn't break**: text doesn't overflow / truncate; nothing overlaps
- **Interaction switches**: mobile uses hamburger + tap, desktop uses hover + mouse, tablet must support both
- **Touch targets**: mobile tap area >= 44x44pt (WCAG 2.5.5 / Apple HIG)
- **Media swapping**: `<picture srcset>` / `image-set()` loads the right resolution per DPR
- **Readable typography**: base font-size >= 14px; line height 1.4–1.6; use rem/em so user zoom works
- **Keyboard navigation**: tab order survives responsive reordering

### Common RWD bug patterns (each deserves its own TC)
- **Hard-coded px**: layout shatters under user font zoom
- **Hover-only interactions**: mobile has no hover; element stuck in hover state
- **Hidden but not unmounted**: `display:none` element still in DOM -> selectors mistakenly hit invisible nodes
- **Missing viewport meta**: phone shows a shrunken desktop view, no reflow
- **Untested transition zone**: 768–1023 layout broken even when mobile/desktop are fine
- **No image lazy-load / missing srcset**: mobile downloads desktop hero image, kills bandwidth

### Strategy
- **E2E**: run core flows at three representative viewports (mobile 375 / tablet 768 / desktop 1280)
- **Visual regression**: cross-viewport diff with Percy / Chromatic
- **Boundary thinking**: breakpoint +/- 1px (e.g. 767 vs 768) should switch cleanly, never ambiguously
- **Real devices**: CSS emulators != real mobile (iOS Safari bottom bar, Android IME height, Dynamic Island)

## Test Type Reference
| Type | Definition | Automation | Frequency |
|------|------|--------|------|
| **Smoke** | Quick post-build validation of core paths | Should automate | Every build |
| **Regression** | Confirm changes didn't break existing features | Should automate | Every change |
| **Functional** | Verify business requirements | Partial | Every feature |
| **Integration** | Verify module interactions | Should automate | Every integration |
| **E2E** | Full user journeys | Critical paths only | Before release |
| **Performance** | Load, response time, resource usage | Tool-assisted | Periodic |
| **Security** | Vulnerabilities, auth, encryption | Partial tooling | Before release |
| **Exploratory** | Unscripted, experience-driven | Manual | New features |
| **Acceptance** | Conformance to business criteria | Partial | Before release |

## QA Metrics
| Metric | Formula | Target |
|------|------|------|
| **Execution rate** | (Pass + Fail) / Total | > 95% |
| **Pass rate** | Pass / (Pass + Fail) | > 95% |
| **Defect density** | Bugs / KLOC | Lower is better |
| **Defect removal efficiency** | Pre-release bugs / total bugs | > 90% |
| **Regression pass rate** | Regression Pass / Regression Total | > 98% |
| **Automation coverage** | Automated TCs / total TCs | Per pyramid layer |
| **Mean time to repair** | Fix complete - bug filed | Lower is better |

## API Testing Methodology
### Schema-driven testing
- Treat OpenAPI 3.x / Swagger 2.0 / JSON Schema as the single source of truth
- Auto-generate request params, boundary values, error paths, and response-shape assertions
- Tools: Schemathesis (HTTP, property-based, end-to-end), Dredd (contract smoke)
- Fastest start: feed your existing spec to Schemathesis and run it — equivalent to automated "ISTQB boundary + equivalence partitioning"

### Contract testing (consumer-driven)
- **Pact** is the de-facto standard: consumers describe expectations, the broker stores them, providers verify
- Closes the microservices "green on my side, breaks on integration" gap
- Flow: consumer's unit tests -> pact JSON -> broker -> provider CI pulls + replays -> verify
- Division of labor vs schema testing: schema tests verify shape; contract tests verify that two services agree on that shape

### Property-based testing
- Don't write fixed examples; describe properties that should hold for all valid inputs
- Tools: `hypothesis` (Python), `fast-check` (JS), `schemathesis` (HTTP layer)
- Example: for any valid `POST /orders` payload, the response must include an `id` that is a UUID
- Catches boundary bugs far faster than hand-enumerated cases

### Authentication patterns
| Pattern | How to test |
|------|--------|
| Bearer token | `Authorization: Bearer <jwt>`; verify expiry, bad signature, missing scope |
| OAuth2 client_credentials | POST `/oauth/token` first, then attach access_token; verify token refresh on expiry |
| API key header | `X-API-Key: <key>`; verify quota exhaustion, IP allowlist |
| mTLS | Mutual cert; verify expired cert, CN mismatch |

### Idempotency keys
- Client sends `Idempotency-Key: <uuid>`; server returns the same result for the same key within N minutes
- Must-test: same key replay returns 200 + identical body; different key counts as new request
- Use cases: payments, `POST /payments`, external webhook redelivery

### Rate-limit handling
- Convention: HTTP 429 + `Retry-After: <seconds>` header
- Client strategy: exponential backoff (initial 1s, cap 60s, jitter +/- 20%)
- Test focus: hammer until 429 fires, retry after `Retry-After` succeeds, exceed max retries fails cleanly

### Pagination strategies
| Strategy | Use case | Trade-off |
|------|------|------|
| Offset/limit | Small datasets, UI page numbers | Slow at deep offsets; items shift when data mutates |
| Cursor | Streams / feeds | Cannot jump pages; must handle cursor invalidation |
| Keyset | Large time-series tables | Requires a stable sort key; no arbitrary sort |

### Error response conventions
- **RFC 7807 problem+json**: `{ type, title, status, detail, instance }` — REST industry consensus
- **GraphQL `errors[]`**: each error carries `message` / `path` / `extensions.code`
- Must-test: error paths conform to your chosen shape (not just "status code matches")
- Anti-pattern: 200 OK with `{ "error": "..." }` in the body — breaks monitoring, retry, and logging

## Flaky Test Root-Cause Taxonomy
> Most "flaky" test failures aren't flakes — they're root-cause-able. The
> optimizer's `broken` classification is for when three consecutive failures
> share an error signature; that's a real bug, not a flake. Genuine flakes
> almost always fall into one of these five buckets:

### 1. Race conditions
- **Smell**: re-running passes; looping 100 times fails 1-2 times
- **Fix**: use explicit waits (`wait_for_response` / `expect(locator).to_be_visible`), never `sleep(N)`
- **Example**: click triggers AJAX, the next assertion runs before the response arrives -> occasional pre-response assert

### 2. External dependencies
- **Smell**: fails in CI but not locally; failure rate spikes during peak network hours
- **Fix**: mock / VCR / contract-test third parties; freeze time; retry-with-backoff for network calls
- **Example**: integration test hits real Stripe sandbox; sandbox blips -> test red, your code is fine

### 3. Order-dependent tests
- **Smell**: passes in isolation, fails in the full suite; flipping `-p random` seed changes the result
- **Fix**: every test owns its setup/teardown; never share module-level state or pre-seeded DB rows
- **Example**: test_a inserts a user; test_b assumes empty DB -> test_b fails when scheduled after test_a

### 4. Time-sensitive tests
- **Smell**: fails overnight; fails crossing day / timezone boundaries; broken the weekend DST flips
- **Fix**: use freezegun / jest fake timers to lock the clock; never assert against `datetime.now()` directly
- **Example**: order's `created_at` uses server time; assertion checks the word "today" -> test crossing midnight fails

### 5. Resource leaks
- **Smell**: suite gets slower as it runs; the last tests time out; CI runner OOMs
- **Fix**: fixtures use yield/teardown to close sockets / files / processes / pages
- **Example**: Playwright tests forget to close context -> 50 chromium processes accumulate, CI runner runs out of memory

## Test Doubles (Mock / Stub / Fake / Spy)
> Use Martin Fowler's canonical four-type breakdown. Each has a distinct
> purpose — don't lump them all under "mock" and don't reach for the
> heaviest one when the lightest fits.

### Stub — returns canned data
- **Trait**: provides a fixed response; does **not** verify how it was called
- **Use when**: isolating an external dependency to test state changes in the SUT (state verification)
- **Example**: stub `get_exchange_rate()` to always return 30.0 while testing currency conversion logic

### Mock — canned data + call verification
- **Trait**: returns a fixed response **and** asserts that "this method was called N times with these args" (behavior verification)
- **Use when**: verifying an interaction contract — e.g. "placing an order must call audit_log.write()"
- **Example**: `mock_audit.assert_called_once_with(order_id=42)` — behavior assertion

### Fake — working implementation, simplified
- **Trait**: real logic, runs end-to-end, but simpler than production (in-memory vs DB; local vs cloud)
- **Use when**: integration tests need a working dependency without spinning up the real service
- **Example**: SQLite in-memory instead of Postgres; fakeredis instead of ElastiCache

### Spy — wraps a real object and records calls
- **Trait**: the wrapped object **actually executes**; the spy records call count / args alongside
- **Use when**: you need real behavior *and* the ability to inspect the interaction afterwards
- **Example**: spy wraps `EmailService.send`; assert `spy.call_count == 1` after the flow runs

### Anti-patterns
- **Over-mocking**: mock every dependency -> the test asserts mock behavior, not production behavior
- **Mocking what you don't own**: mock internals of a third-party SDK -> SDK upgrade slips through, test stays green, production breaks
- **Mock everything then test nothing**: layers of mocks leave the SUT's real logic unexercised; coverage looks high but provides zero protection
- **Heuristic**: prefer state verification (stub + fake); reach for behavior verification (mock) only when the interaction itself is the requirement

## Test Data Management
### Factories
- `factory_boy` (Python), `faker` (multi-language): declarative data generation with sane defaults + per-test overrides
- More flexible than static fixture files: each test tweaks `OrderFactory(amount=999)` and other fields auto-fill
- Best for: complex object construction paths, models with many fields

### Fixtures
- `pytest.fixture` / Jest `beforeEach`: reusable setup with scope control (function / class / session)
- Best for: small, static setup shared across many tests

### Isolation strategies
- **Per-test transaction rollback**: DB tests run inside a transaction that rolls back on teardown -> no residue
- **Per-test temp dirs**: filesystem tests use `tmp_path`; automatic cleanup
- **Per-test namespace**: containers / k8s integration tests get a fresh namespace per case

### Time mocking
- `freezegun` (Python): `with freeze_time("2026-05-16"):` pins `datetime.now()`
- Jest fake timers: `jest.useFakeTimers()` controls `Date.now()` + `setTimeout`
- iOS XCTest: dependency-inject a `Clock` abstraction; tests inject a fake clock

### Database seeding
- **Per-suite seed**: bulk-load once before the suite; all tests share -> fast but couples tests (order-dependence risk)
- **Per-test seed**: each test owns its setup -> slower but isolated (preferred default)
- Compromise: load reference data (countries / categories / roles) per-suite; transactional data per-test

### Fresh data vs fixture sharing — the trade-off
| Shared fixtures (per-suite) | Fresh data (per-test) |
|---|---|
| Faster | Cleanly isolated |
| Risks order-dependence | No cross-test pollution |
| Good for heavy, slow setup | Good for the main business cases |
| Best for: reference tables | Best for: transactional / mutation cases |

## CAPTCHA Testing Strategy

CAPTCHA (reCAPTCHA / hCaptcha / Cloudflare Turnstile) is where automated test runs most often stall. **In 90% of scenarios, the right answer is not to "solve" the CAPTCHA — it's to bypass it.**

### Tier 1: Bypass — first choice

| Method | How | When it fits |
|---|---|---|
| **reCAPTCHA test keys** | Swap staging to Google's official test pair (site: `6LeIxAcTAAAAAJcZVRqyHh71UMIEGNQ_MXjiZKhI` / secret: `6LeIxAcTAAAAAGG-vFI1TnRWxMZNFuojJ4WifJWe`) — every challenge auto-passes | Your own site, backend control |
| **Feature flag** | Backend reads `DISABLE_CAPTCHA_IN_STAGING=true`; staging skips the check entirely | Your own site |
| **Test-mode header** | QA runs send `X-Test-Mode: <shared-secret>`; backend bypasses on match | Your own site |
| **IP allowlist** | QA runner IPs are whitelisted past CAPTCHA | Fixed QA infrastructure |

**Strongly preferred: reCAPTCHA test keys** — zero backend code, official Google support, 100% pass rate, no cost.

### Tier 2: Degrade gracefully — when you can't change the backend

- Detect the CAPTCHA iframe, screenshot it, mark the test as `external_dependency` and skip downstream assertions
- mk-qa-master's optimizer classifies consecutive CAPTCHA-caused failures as `external` rather than `broken` or `flaky`
- Skip CAPTCHA-protected paths in CI; use Tier 3 (manual or AI vision) only on local dev

### Tier 3: AI visual judgment (v0.7.0+) — last resort

The planned `solve_visual_challenge` tool in mk-qa-master:

1. Detect the CAPTCHA iframe (reCAPTCHA v2 / hCaptcha image grid)
2. Screenshot the challenge + extract tile selectors
3. Return the screenshot to the AI client (Claude / Cursor — already vision-capable)
4. AI replies "click tiles [0, 4, 7]"
5. Runner executes the click chain, submits, continues

**Constraints:**

- Workable on reCAPTCHA v2 / hCaptcha image grids, 60–80% success rate
- **Not workable** on reCAPTCHA v3 or Cloudflare Turnstile — no visible challenge, only behavior scoring. Mitigation is stealth plugins + realistic mouse movement + IP reputation, not visual solving.
- Google may ban sessions / IPs that look automated — **do not run against production**
- Legal: only on your own sites, or client sites with explicit authorization. Third-party sites usually violate TOS.

### Decision flow

```
Your site, backend access?       -> Tier 1 (test keys)
Your site, can't touch backend?  -> Tier 1 variant (proxy or IP allowlist)
Client site, authorized?         -> Tier 1, fallback Tier 3
Not your site at all?            -> Don't run CAPTCHA bypass or solvers
```

## Edge Vision Inference Testing (v1.1.0+)

`QA_RUNNER=edge` chains RTSP stream + YOLO inference + pytest assertions into a single test flow. This section captures the domain principles that differ most from typical web / mobile testing.

### Core principles

- **Detection correctness must use IoU thresholds — never compare exact coordinates.** AI output is inherently fuzzy; pixel-level matching tests randomness, not business correctness. `mk_qa_master.edge.metrics.match_detection` uses IoU with a 0.5 default threshold.
- **Performance and correctness are equally important.** A correct answer half a beat late is, in production, a failure. Every detection test should be paired with throughput / latency assertions, and p95 latency reflects reality better than mean.
- **Every input must be traceable back to a frame number.** Burn frame indices into the video corner or read `cv2.CAP_PROP_POS_FRAMES`. Without that, failure reports can only say "detection missed at some time" — not reproducible.
- **Resilience scenarios are first-class citizens.** Stream disconnects, corrupt frames, unexpected codecs must not crash the test. At minimum cover: (1) mid-test `kill ffmpeg` to simulate disconnect; (2) deliberately corrupted GOP; (3) `tc qdisc netem` to inject jitter.
- **Track the empty-frame false-positive rate separately.** A healthy model should NOT produce detections against solid-color or noise frames. Every suite should include at least one `test_empty_frame_no_false_positives`.

### Edge runner-specific risks

- **mediamtx + ffmpeg startup is order-sensitive.** The runner uses socket readiness probing to mitigate, but CI still races occasionally. If you see "RTSP open failed", check whether `start_rtsp_source`'s timeout was shortened.
- **YOLO model load must be session-scoped.** The `backend` fixture is session-scoped, not function-scoped. Accidentally narrowing it to function scope reloads the model per test case and blows the suite's wall-clock.
- **Without `QA_JETSON_HOST` / `QA_INFERENCE_ENDPOINT`, the runner uses LocalYolo.** v1.1 does not support remote inference — calling `.infer()` raises NotImplementedError. Phase 3 (v1.2) wires it up.
- **The vendor-host blacklist refuses Dahua / Hikvision / etc. by default.** Set `QA_EDGE_ALLOW_VENDOR_HOSTS=true` only for cameras you own; otherwise tests receive a `forbidden_vendor_host` envelope.

### Recommended SLA defaults

| Scenario | min_fps | latency_sla_ms | iou_threshold |
|---|---|---|---|
| Desktop yolov8n development | 25 | 40 | 0.5 |
| Jetson Nano | 15 | 70 | 0.5 |
| Jetson Orin Nano | 30 | 25 | 0.6 |
| Cloud GPU inference service | 60 | 16 | 0.6 |

Take these as starting points and tune. Fail the very first time SLA is breached — don't wait until repeated regressions accumulate.
"""


# ---------------------------------------------------------------------------
# Per-project knowledge slots — methodology covers the "how to test",
# these cover the "what's specific about THIS product". Both layers are
# needed to escape monkey-testing. Same five slot headings translated.
# ---------------------------------------------------------------------------
_DOMAIN_TODO_SECTIONS_ZH = """## 你的業務規則
- TODO: 領域邏輯 / 折扣計算 / 限購規則 / 會員等級 / 優惠券規範 ...

## 你的歷史 Bug / 回歸點
- TODO: 已修 ticket ID + 簡述 + 期望行為 + 觸發條件 + fix reference

## 你的標準斷言文字
- TODO: UI 標準文案精確字元 / 錯誤訊息 / 成功提示 / CTA 標籤

## 你的 User Journeys
- TODO: 多步驟業務流程：登入 → 主要操作 → 完成 → 驗證結果

## 你的技術約束
- TODO: Test env URL / Test user / 必要 header / 固定隨機種子方法
"""


_DOMAIN_TODO_SECTIONS_EN = """## Your Business Rules
- TODO: domain logic / discount math / purchase limits / membership tiers / coupon rules ...

## Your Historical Bugs / Regression Points
- TODO: fixed ticket ID + summary + expected behavior + trigger condition + fix reference

## Your Standard Assertion Strings
- TODO: exact UI copy / error messages / success toasts / CTA labels

## Your User Journeys
- TODO: multi-step flows: login -> primary action -> completion -> verify result

## Your Technical Constraints
- TODO: test env URLs / test users / required headers / deterministic-seed mechanism
"""


def _builtin_for_lang(lang: str) -> str:
    """Concat universal methodology + domain TODO slots for the chosen language.

    `lang` is expected to already be normalized to `en` or `zh-tw` by
    config.QA_LANG; we still defensively check for `zh-tw` and fall back
    to English so a bad runtime override doesn't crash the server.
    """
    if lang == "zh-tw":
        return _UNIVERSAL_METHODOLOGY_ZH + _DOMAIN_TODO_SECTIONS_ZH
    return _UNIVERSAL_METHODOLOGY_EN + _DOMAIN_TODO_SECTIONS_EN


_BUILTIN_HEADER_ZH = (
    "# QA Knowledge — Universal Testing Methodology (built-in fallback)\n\n"
    "> 這份是 mk-qa-master 內建的通用 QA 方法論，整理自 ISTQB / Google 測試金字塔 / 業界 mobile QA 標準\n"
    "> + API 測試、Flaky 根因分類、測試替身（Fowler 四分類）、測試資料管理。\n"
    "> 任何測試專案都可以套用 — **但缺少你的領域知識**（業務規則 / 回歸點 / 標準文案）\n"
    "> 就還是會偏向 monkey testing。解法：執行 init_qa_knowledge tool，\n"
    "> 在受測專案根產生 qa-knowledge.md、把下面五個「你的 XXX」TODO 區段填上你的領域內容。\n\n"
)


_BUILTIN_HEADER_EN = (
    "# QA Knowledge — Universal Testing Methodology (built-in fallback)\n\n"
    "> This is mk-qa-master's built-in universal QA methodology, distilled from\n"
    "> ISTQB principles, Google's test pyramid, industry mobile QA standards,\n"
    "> plus API testing, flakiness root-cause taxonomy, test doubles\n"
    "> (Fowler's four-type breakdown), and test data management.\n"
    "> Any testing project can apply this — **but without your domain knowledge**\n"
    "> (business rules, regression points, standard copy), it still drifts toward\n"
    "> monkey testing. Fix: run the `init_qa_knowledge` tool to scaffold\n"
    "> `qa-knowledge.md` in your project root, then fill the five `Your XXX`\n"
    "> TODO sections below with your domain content.\n\n"
)


_STARTER_HEADER_ZH = (
    "# QA Knowledge — {project_name}\n\n"
    "> 給 mk-qa-master 讀的領域知識。get_qa_context() 會把這份內容暴露給 AI，\n"
    "> 用於決定要測什麼 + 把規則印進產出 test 的 `# Business context:` 區段。\n"
    "> **規則**：以 H2 (`##`) 區段為單位、client 可指定 section 拉取單一段（partial match）。\n\n"
    "> ---\n"
    "> **上半部「通用測試方法論」**（ISTQB / 邊界值 / 測試金字塔 / 回歸策略 / Mobile checklist / "
    "QA metrics / API 測試 / Flaky 根因 / 測試替身 / 測試資料）\n"
    "> 由 mk-qa-master 預載。一般不需要動；場景不適用某些方法論可以刪除對應 H2 段落。\n"
    "> \n"
    "> **下半部「你的 XXX」TODO 區段**才是讓測試脫離 monkey 等級的關鍵 — 請填入你的領域業務規則。\n"
    "> ---\n\n"
)


_STARTER_HEADER_EN = (
    "# QA Knowledge — {project_name}\n\n"
    "> Domain knowledge for mk-qa-master to read. `get_qa_context()` exposes this\n"
    "> file to the AI so it can decide what to test and inject your rules into the\n"
    "> `# Business context:` block of every generated test.\n"
    "> **Convention**: split topics with H2 (`##`); clients can pull a single\n"
    "> section by name (partial match, case-insensitive).\n\n"
    "> ---\n"
    "> The **upper half (universal testing methodology)** — ISTQB / boundary values /\n"
    "> test pyramid / regression strategy / mobile checklist / QA metrics / API\n"
    "> testing / flakiness taxonomy / test doubles / test data management — ships\n"
    "> preloaded by mk-qa-master. You usually don't need to touch it; delete any\n"
    "> H2 sections that don't apply to your context.\n"
    "> \n"
    "> The **lower half (`Your XXX` TODO sections)** is what lifts tests above\n"
    "> monkey level — fill these with your domain business rules.\n"
    "> ---\n\n"
)


def _builtin_defaults() -> str:
    if QA_LANG == "zh-tw":
        return _BUILTIN_HEADER_ZH + _builtin_for_lang("zh-tw")
    return _BUILTIN_HEADER_EN + _builtin_for_lang("en")


def _starter_template() -> str:
    if QA_LANG == "zh-tw":
        return _STARTER_HEADER_ZH + _builtin_for_lang("zh-tw")
    return _STARTER_HEADER_EN + _builtin_for_lang("en")


def load_context(section: str | None = None) -> dict:
    if not QA_KNOWLEDGE_PATH.is_file():
        # Fallback: serve universal methodology so a fresh install isn't useless.
        builtin = _builtin_defaults()
        sections = _parse_sections(builtin)
        if section:
            s_low = section.lower()
            for name, content in sections.items():
                if name.lower() == s_low or s_low in name.lower():
                    return {
                        "path": str(QA_KNOWLEDGE_PATH),
                        "exists": False,
                        "using_builtin_defaults": True,
                        "lang": QA_LANG,
                        "section": name,
                        "content": content,
                        "hint": (
                            "Built-in fallback. Run init_qa_knowledge for "
                            "project-specific knowledge."
                            if QA_LANG == "en"
                            else "這是內建 fallback。要專案專屬知識請執行 init_qa_knowledge。"
                        ),
                    }
            return {
                "path": str(QA_KNOWLEDGE_PATH),
                "exists": False,
                "using_builtin_defaults": True,
                "lang": QA_LANG,
                "section": section,
                "content": None,
                "available_sections": list(sections.keys()),
                "error": f"Section not found: {section} (even in built-in defaults)",
            }
        return {
            "path": str(QA_KNOWLEDGE_PATH),
            "exists": False,
            "using_builtin_defaults": True,
            "lang": QA_LANG,
            "hint": (
                f"{QA_KNOWLEDGE_PATH} not found; returning built-in universal QA "
                "methodology. Run init_qa_knowledge for project-specific knowledge."
                if QA_LANG == "en"
                else (
                    f"未找到 {QA_KNOWLEDGE_PATH}，回傳內建通用 QA 方法論。"
                    "若要專案專屬知識，請執行 init_qa_knowledge tool。"
                )
            ),
            "full_content": builtin,
            "sections": list(sections.keys()),
        }

    try:
        text = QA_KNOWLEDGE_PATH.read_text(encoding="utf-8")
    except OSError as e:
        return {
            "path": str(QA_KNOWLEDGE_PATH),
            "exists": False,
            "error": f"{type(e).__name__}: {e}",
        }

    sections = _parse_sections(text)
    if section:
        s_low = section.lower()
        for name, content in sections.items():
            if name.lower() == s_low or s_low in name.lower():
                return {
                    "path": str(QA_KNOWLEDGE_PATH),
                    "exists": True,
                    "section": name,
                    "content": content,
                }
        return {
            "path": str(QA_KNOWLEDGE_PATH),
            "exists": True,
            "section": section,
            "content": None,
            "available_sections": list(sections.keys()),
            "error": f"Section not found: {section}",
        }
    return {
        "path": str(QA_KNOWLEDGE_PATH),
        "exists": True,
        "full_content": text,
        "sections": list(sections.keys()),
    }


def init_qa_knowledge(overwrite: bool = False) -> dict:
    """Scaffold a starter qa-knowledge.md at QA_KNOWLEDGE_PATH.

    Idempotent by default: refuses to overwrite an existing file unless
    overwrite=True. The starter bundles the universal methodology plus
    empty TODO domain sections, so the user has reference material and
    edit targets in one file. Methodology language is driven by
    config.QA_LANG (`en` default; `zh-tw` for the original Traditional
    Chinese content).
    """
    if QA_KNOWLEDGE_PATH.is_file() and not overwrite:
        try:
            existing_size = QA_KNOWLEDGE_PATH.stat().st_size
        except OSError:
            existing_size = -1
        return {
            "path": str(QA_KNOWLEDGE_PATH),
            "created": False,
            "existing_bytes": existing_size,
            "reason": (
                "File exists; pass overwrite=true to replace (back it up first)."
                if QA_LANG == "en"
                else "檔已存在；要覆蓋請傳 overwrite=true（建議先備份）"
            ),
        }
    try:
        QA_KNOWLEDGE_PATH.parent.mkdir(parents=True, exist_ok=True)
        content = _starter_template().format(project_name=QA_KNOWLEDGE_PATH.parent.name)
        QA_KNOWLEDGE_PATH.write_text(content, encoding="utf-8")
    except OSError as e:
        return {
            "path": str(QA_KNOWLEDGE_PATH),
            "created": False,
            "error": f"{type(e).__name__}: {e}",
        }
    return {
        "path": str(QA_KNOWLEDGE_PATH),
        "created": True,
        "bytes": len(content),
        "lang": QA_LANG,
        "next_step": (
            "Edit this file and replace each `Your XXX` TODO section with your "
            "domain business rules / historical bugs / standard assertion strings / "
            "user journeys / technical constraints. From then on, get_qa_context "
            "reads your version directly (no more fallback)."
            if QA_LANG == "en"
            else (
                "編輯這個檔案、把「你的 XXX」TODO 區段換成你的業務規則 / 歷史 Bug / "
                "標準斷言文字 / User Journeys / 技術約束。"
                "之後 get_qa_context 會直接讀你的版本（不再回 fallback）。"
            )
        ),
    }


def _parse_sections(text: str) -> dict[str, str]:
    """Split markdown by ## H2 headers -> {section_name: body_text}.

    Why H2-only: H1 is reserved for the document title, H3+ are nested
    detail (subsections within a topic). Treating H2 as the natural "topic"
    boundary matches how authors typically structure a knowledge doc.
    """
    sections: dict[str, str] = {}
    current_name: str | None = None
    current_lines: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            if current_name is not None:
                sections[current_name] = "\n".join(current_lines).strip()
            current_name = m.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)
    if current_name is not None:
        sections[current_name] = "\n".join(current_lines).strip()
    return sections
