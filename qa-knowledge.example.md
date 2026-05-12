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
>
> 下列範例以泛例電商情境呈現，**請依你的領域全部替換掉**。

## 業務規則
- 折抵點數：1 點折抵 1 元；單筆訂單最高折抵金額不超過 50%
- 會員等級：依累積消費分級；達門檻自動升等、高階等級享商品折扣
- 優惠券：同一帳號限領一次（後端用 idempotency key 控制冪等）
- 訂單：成立 N 分鐘內可線上取消；超過時間僅能走客服退款流程

## 歷史 Bug / 回歸點
- BUG-001（已修）：優惠券連點兩次拿到兩張 → race condition
  - 期望：第二次點擊應回 409 + 顯示「您已領取過此優惠券」
  - 觸發條件：100ms 內連續觸發兩次 click
- BUG-002（已修）：登出後購物車未清空
  - 期望：logout API 200 後本機 cart store 也應 reset 為空
- BUG-003（追蹤中）：特定瀏覽器版本下 IME 切換時搜尋框閃爍
  - Workaround：暫不在該版本跑相關 E2E

## 標準斷言文字
- 錯誤密碼 → 「帳號或密碼錯誤」（**精確字串**、非「密碼錯誤」、非英文）
- 優惠券已領 → 「您已領取過此優惠券」
- 庫存不足 → 「商品已售完」
- 訂單成立成功 → 「訂單已建立，編號 #XXXXXX」（6 位數字）

## User Journeys
- **happy-path-checkout**：登入 → 加入購物車 → 套用優惠券 → 結帳 → 訂單成立頁
- **coupon-redemption**：登入 → 列表頁領券 → 加購商品 → 結帳時套用 → 驗證折扣金額
- **tier-upgrade**：以累積消費接近升等門檻的帳號 → 完成一筆訂單 → 應即時升等 → 全站可見等級 badge
- **logout-cart-reset**：登入 → 加購 3 件 → 登出 → 重新登入 → 購物車應為空

## 技術約束
- Test env URL：https://uat.your-domain.example/
- Test user：qa@example.com / `TestPass123!`
- Backend idempotency header：X-Idempotency-Key（UUID v4）
- Cookie：`session_id`（HttpOnly）、`csrf_token`（與 meta tag 同名）
- 帶 `?qa_seed=<int>` query 可固定後端隨機資料（折扣計算 / 推薦商品 等）讓測試 deterministic
