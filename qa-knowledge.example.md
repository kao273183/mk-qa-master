# QA Knowledge — Domain Examples (cross-domain)

> 這份是 `qa-knowledge.md`「**你的 XXX**」5 個領域區段的**填滿範本**。
> 每個區段都示範 4 種常見 web app 架構，找你最像的那個照模仿、不像的略過：
>
> - 🛒 **電商 / 購物**（cart / coupon / checkout / inventory）
> - 🏢 **SaaS / B2B**（multi-tenant / 權限 / 計費 / quota）
> - 📰 **內容 / 社群**（post / moderation / feed / 反濫用）
> - 💰 **金融 / 銀行**（transfer / OTP / KYC / 日限額）
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
> 2. 在下方找你的 web app 架構 → 抄結構（不是抄字串）
> 3. 把你產品真實的業務規則 / 歷史 bug / 標準文案填進你的 `qa-knowledge.md`
>
> **注意**：本檔只放領域區段（不含方法論），避免和內建重複。

## 你的業務規則

### 🛒 電商
- 折抵點數：1 點折抵 1 元；單筆訂單最高折抵金額不超過 50%
- 同一張優惠券一個帳號限領一次（後端用 idempotency key 控制冪等）
- 訂單成立 N 分鐘內可線上取消；超過僅能走客服退款流程
- 庫存扣減在「按下結帳」時鎖定 5 分鐘；逾時未付款釋放

### 🏢 SaaS (B2B)
- 免費方案上限：5 個專案、每月 1,000 API call、單檔上傳 ≤ 10MB
- 角色權限：Owner 可邀請成員與計費；Admin 管理員可管理權限；Member 只能讀寫被分派的資源
- 訂閱降級時：超出新方案上限的資源凍結為 read-only、不刪除
- API 速率限制：依方案分級（Free 60/min、Pro 600/min、Enterprise 自訂）

### 📰 內容 / 社群
- 同 user 發文頻率：30 秒內最多 1 篇；24 小時內最多 50 篇
- 含敏感字詞的貼文進入審核佇列（不直接公開）
- 違規 3 次自動暫停發文 7 天；申訴成功可解除
- 拉黑列表單向生效：A 拉黑 B → A 不見 B 的內容、B 不見 A 的 profile

### 💰 金融 / 銀行
- 單日轉帳上限 NT$30,000；跨行轉帳手續費 NT$15
- 大額轉帳（> NT$30,000）需二階段驗證（OTP + 推播確認）
- 帳戶餘額不可為負；扣款超過餘額應回 InsufficientFunds 而非允許負值
- 交易紀錄保留至少 7 年（合規要求）

## 你的歷史 Bug / 回歸點

### 🛒 電商
- **BUG-101（已修）**：優惠券連點兩次拿到兩張 → race condition
  - 期望：第二次點擊應回 409 + 顯示「您已領取過此優惠券」
  - 觸發條件：100ms 內連續兩次 click
- **BUG-102（已修）**：登出後購物車未清空 → logout API 200 後本機 cart store 應 reset 為空

### 🏢 SaaS
- **BUG-201（已修）**：切換 tenant 後資料快取未刷新 → 顯示前一個 tenant 的資料
  - 觸發：1 分鐘內切換 tenant 並打開原本開過的頁面
- **BUG-202（追蹤中）**：webhook 大量同時觸發時 rate-limiter 用 user-agent 誤判
  - Workaround：改用 X-Tenant-ID 作為 rate key

### 📰 內容 / 社群
- **BUG-301（已修）**：連續按 like 5 次只增加 1 次計數 → 客戶端 debounce 太強
- **BUG-302（已修）**：刪文後通知中心仍顯示「有人留言」→ 改成 soft-delete + 過濾已刪文章

### 💰 金融 / 銀行
- **BUG-401（已修）**：跨時區交易日期顯示錯誤（UTC vs local）→ 統一儲存 UTC、顯示時轉 local
- **BUG-402（高優先）**：並發小額轉帳可能跳過餘額檢查 → 改用 row-level lock

## 你的標準斷言文字

### 🛒 電商
- 錯誤密碼 → 「帳號或密碼錯誤」（不是「密碼錯誤」、不是英文）
- 優惠券已領 → 「您已領取過此優惠券」
- 庫存不足 → 「商品已售完」
- 訂單成立成功 → 「訂單已建立，編號 #XXXXXX」（6 位數字）

### 🏢 SaaS
- 權限不足 → 「您沒有權限執行此操作」（不是「403 Forbidden」、不是英文）
- 額度用完 → 「本月 API 額度已用完，請升級方案」
- Trial 到期 → 「您的試用期已結束」
- 邀請已送出 → 「邀請信已寄出至 user@example.com」（含實際 email）

### 📰 內容 / 社群
- 內容違規 → 「此內容違反社群守則」
- 發文太快 → 「發文頻率過高，請稍候再試」
- 帳號被停權 → 「您的帳號已被暫停，請聯繫客服」
- 已被拉黑 → 「無法檢視此用戶」（不應提示「對方已拉黑你」，避免敵意）

### 💰 金融 / 銀行
- 餘額不足 → 「餘額不足，請確認後重試」
- OTP 錯誤 → 「驗證碼錯誤或已過期」
- 達單日上限 → 「已達單日轉帳上限 NT$30,000」
- 帳戶凍結 → 「帳戶異常，請洽客服 (02)XXXX-XXXX」（含實際客服電話）

## 你的 User Journeys

### 🛒 電商
- **happy-path-checkout**：登入 → 加入購物車 → 套用優惠券 → 結帳 → 訂單成立頁
- **coupon-redemption**：登入 → 領券 → 加購商品 → 結帳套用 → 驗證折扣金額
- **vip-upgrade**：以累積消費接近升等門檻的帳號 → 完成一筆訂單 → 即時升等 → 全站可見 badge

### 🏢 SaaS
- **onboarding**：註冊 → 驗證信 → 創立第一個專案 → 邀請第一個成員 → 完成入門教學
- **plan-upgrade**：Free 用戶觸及上限 → 看到升級提示 → 選方案 → 付款 → 解除限制
- **member-revoke**：Owner 移除成員 → 該成員 active session 應在 N 秒內失效

### 📰 內容 / 社群
- **first-post**：註冊 → 完成個人資料 → 發第一篇文 → 收到第一個 like → 互動入門完成
- **moderation-flow**：用戶檢舉 → 進入審核佇列 → moderator 判定 → 通知雙方
- **block-and-unblock**：A 拉黑 B → A/B 互相不可見 → A 解除拉黑 → 可見性恢復

### 💰 金融 / 銀行
- **first-transfer**：註冊 → KYC → 綁定帳戶 → 第一筆轉帳（含 OTP）→ 完成
- **password-reset**：忘記密碼 → 身分驗證 → OTP → 設新密碼 → 強制所有 session 重新登入
- **large-transfer**：發起 > NT$30,000 轉帳 → 觸發 OTP + 推播 → 雙重確認 → 完成

## 你的技術約束

> 這個區段通常跨 domain 都類似（環境 / 帳號 / header / 隨機種子），不分架構列。

- **Test env URL**：UAT / staging / local 各環境的入口 URL
- **Test user**：QA 專用帳號 + 密碼（不要用真實 user 帳號）
- **Backend idempotency / tracing header**：例如 `X-Idempotency-Key` (UUID v4)、`X-Request-Id`
- **Auth cookie / token**：例如 `session_id`（HttpOnly）、`csrf_token`（meta tag 同名）
- **Deterministic seed**：例如 `?qa_seed=<int>` 可固定後端隨機資料（折扣計算 / 推薦商品 / 推送演算法）
- **Feature flag overrides**：例如 `?ff=newCheckout:on,oldCart:off` 可在 QA 環境臨時切換
- **Locale**：例如 `Accept-Language: zh-TW` 才會載入正確的標準斷言文字
