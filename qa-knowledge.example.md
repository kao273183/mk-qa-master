# QA Knowledge — Domain Example

> 這份是 `qa-knowledge.md`「**你的 XXX**」5 個領域區段的**填滿範本**，
> 用泛例電商情境呈現「填好後長什麼樣」。
>
> ## 跟其他元件的分工
>
> | 元件 | 提供什麼 | 哪裡 |
> |---|---|---|
> | **內建方法論**（ISTQB / 邊界值 / 測試金字塔 / 回歸 / Mobile / Metrics） | 「**怎麼測**」的業界標準觀念 | mcp-test-runner 內建、`get_qa_context` 自動含 |
> | **領域知識**（業務規則 / 歷史 Bug / 標準斷言文字 / Journeys / 技術約束） | 「**測什麼**」的專案專屬知識 | 你的 `qa-knowledge.md`（本檔示範填法） |
>
> ## 怎麼用這份範例
>
> 1. 在受測專案執行 `init_qa_knowledge` MCP tool → 產生含方法論 + 空 TODO 的起手檔
> 2. 參考下面 5 個區段的填法 → 把你的領域內容寫進你的 `qa-knowledge.md`
> 3. 之後 `get_qa_context` 會讀你的版本（方法論 + 你的領域），不再 fallback
>
> **注意**：本檔只放領域區段（不含方法論），避免和內建重複。

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
