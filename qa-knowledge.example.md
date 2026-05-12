# QA Knowledge — Example

> 這是 `mcp-test-runner` 讀的領域知識範本。
> 真實使用時請複製為 `qa-knowledge.md` 放到**受測專案根**（不是這個 MCP repo），
> 或執行 `init_qa_knowledge` MCP tool 自動 scaffold。
>
> `get_qa_context()` 會把這份內容暴露給 AI client，用於：
> 1. 決定要測什麼（業務知識驅動）
> 2. 把規則印進產出 test 的 `# Business context:` 區段
>
> **規則**：以 H2 (`##`) 區段為單位、client 可指定 section 拉取單一段（partial match）。

## 業務規則
- OPEN POINT：1 點折抵 1 元；單筆最高折抵訂單金額 50%
- VIP 等級：累積消費 ≥ NT$5,000 即升級；享所有商品 9 折
- 同一 coupon 一個帳號限領一次（後端用 X-Idempotency-Key 控冪等）
- 訂單成立後 5 分鐘內可取消、5 分鐘後僅能申請退款

## 歷史 Bug / 回歸點
- FLAG-056（已修）：coupon 連點兩次拿兩張 → race condition
  - 期望：第二次點擊應回 409 + 顯示「您已領取過此優惠券」
  - 觸發：在 100ms 內連續觸發兩次 click
- FLAG-072（已修）：登出後 cart 沒清空
  - 期望：logout API 200 後本機 cart store 也 reset
- BUG-091（追蹤中）：iOS Safari < 16 下搜尋框 IME 切換閃爍
  - workaround：暫不在 iOS Safari < 16 跑搜尋 E2E

## 標準斷言文字
- 錯誤密碼 → 「帳號或密碼錯誤」（**非**「密碼錯誤」、**非**「Login failed」）
- Coupon 已領 → 「您已領取過此優惠券」
- 庫存不足 → 「商品已售完」
- 訂單成立成功 → 「訂單已建立，編號 #XXXXXX」（XXXXXX 為 6 位數）

## User Journeys
- **happy-path-checkout**：登入 → 加入購物車 → 套用 coupon → 結帳 → 訂單頁
- **coupon-redemption**：登入 → 列表頁領 coupon → 加購商品 → 結帳套用 → 驗證折扣金額
- **vip-upgrade**：以累積消費 4,999 的帳號 → 完成 NT$100 訂單 → 應即時升 VIP → 全站可見 VIP badge
- **logout-cart-reset**：登入 → 加購 3 件 → 登出 → 重登入 → cart 應為空

## 技術約束
- Test env URL：https://uat.example.com
- Test user：qa@example.com / `TestPass123!`
- Backend idempotency header：X-Idempotency-Key（UUID v4）
- Cookie：`session_id`（HttpOnly）、`csrf_token`（meta tag 同名）
- 帶 `?qa_seed=<int>` 可固定後端隨機資料（折扣計算 / 推薦商品）
