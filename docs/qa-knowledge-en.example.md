# QA Knowledge — Domain Examples (cross-domain)

> This file is a worked example of how to fill the five `Your XXX` domain
> sections of `qa-knowledge.md`. Each section shows four common web-app
> archetypes — find the one closest to your product, copy the structure
> (not the strings), skip the rest:
>
> - **E-commerce / Shopping** (cart / coupon / checkout / inventory)
> - **SaaS / B2B** (multi-tenant / permissions / billing / quotas)
> - **Content / Social** (posting / moderation / feed / anti-abuse)
> - **Finance / Banking** (transfer / OTP / KYC / daily limits)
>
> ## Division of labor with the other layers
>
> | Layer | What it provides | Where it lives |
> |---|---|---|
> | **Built-in methodology** (ISTQB / boundary values / test pyramid / regression / mobile / metrics / API testing / flakiness taxonomy / test doubles / test data) | The "**how to test**" industry-standard concepts | Bundled with mk-qa-master; `get_qa_context` returns it automatically |
> | **Domain knowledge** (business rules / historical bugs / standard assertion strings / journeys / technical constraints) | The "**what to test**" project-specific knowledge | Your own `qa-knowledge.md` (this file shows how to fill it in) |
>
> ## How to use this example
>
> 1. In your project under test, run the `init_qa_knowledge` MCP tool — it scaffolds methodology + empty TODO slots
> 2. Find the archetype closest to your web app below and copy the structure
> 3. Replace the example strings with your product's real business rules, historical bugs, and standard copy
>
> **Note**: this file shows only the domain sections (no methodology) to
> avoid duplicating the built-in fallback.
>
> **Traditional Chinese version**: see [`qa-knowledge.example.md`](qa-knowledge.example.md).

## Your Business Rules

### E-commerce
- Loyalty points: 1 point = $1 off; max discount per order capped at 50% of cart total
- Each coupon code is single-use per account (backend uses an idempotency key to enforce)
- Orders can be canceled online within N minutes of placement; past that, customer service refund flow only
- Inventory deducts on "click checkout" and locks for 5 minutes; releases if payment isn't completed

### SaaS (B2B)
- Free plan caps: 5 projects, 1,000 API calls / month, single upload <= 10MB
- Role permissions: Owner can invite members and manage billing; Admin manages permissions; Member can read/write only assigned resources
- On subscription downgrade: resources exceeding the new plan's caps freeze to read-only, never delete
- API rate limits: tiered by plan (Free 60/min, Pro 600/min, Enterprise custom)

### Content / Social
- Per-user posting frequency: max 1 post per 30 seconds; max 50 posts per 24 hours
- Posts containing sensitive keywords enter the moderation queue (not auto-public)
- Three violations auto-suspend posting for 7 days; successful appeal lifts the suspension
- Block list is one-way: A blocks B -> A doesn't see B's content, B can't see A's profile

### Finance / Banking
- Daily transfer cap NT$30,000; cross-bank transfer fee NT$15
- Large transfers (> NT$30,000) require two-factor verification (OTP + push confirmation)
- Account balance cannot go negative; withdrawals exceeding balance must return InsufficientFunds, never allow a negative
- Transaction records retained for at least 7 years (regulatory requirement)

## Your Historical Bugs / Regression Points

### E-commerce
- **BUG-101 (fixed)**: double-clicking the coupon button claimed two coupons -> race condition
  - Expected: the second click should return 409 + show "You have already claimed this coupon"
  - Trigger: two clicks within 100ms
- **BUG-102 (fixed)**: cart not cleared after logout -> after logout API returns 200, the local cart store must reset to empty

### SaaS
- **BUG-201 (fixed)**: switching tenant didn't invalidate cache -> previous tenant's data shown after switch
  - Trigger: switch tenant within 1 minute and reopen a previously visited page
- **BUG-202 (tracked)**: simultaneous webhook bursts cause rate-limiter to misclassify by user-agent
  - Workaround: switch rate-key to X-Tenant-ID

### Content / Social
- **BUG-301 (fixed)**: clicking like 5 times only incremented count by 1 -> client-side debounce was too aggressive
- **BUG-302 (fixed)**: notification center kept showing "someone replied" after post deletion -> switched to soft-delete + filter out deleted posts

### Finance / Banking
- **BUG-401 (fixed)**: cross-timezone transaction dates displayed incorrectly (UTC vs local) -> store UTC, convert to local at display
- **BUG-402 (high priority)**: concurrent small transfers occasionally bypassed balance check -> switched to row-level lock

## Your Standard Assertion Strings

### E-commerce
- Wrong password -> "Incorrect account or password" (not "Wrong password"; not multiple languages mixed)
- Coupon already claimed -> "You have already claimed this coupon"
- Out of stock -> "Item sold out"
- Order placed successfully -> "Order created, #XXXXXX" (6-digit number)

### SaaS
- Insufficient permission -> "You do not have permission to perform this action" (not "403 Forbidden"; not raw HTTP)
- Quota exhausted -> "You've reached your monthly API quota. Please upgrade your plan."
- Trial expired -> "Your trial period has ended"
- Invitation sent -> "Invitation email sent to user@example.com" (echoes the actual email)

### Content / Social
- Content violation -> "This content violates community guidelines"
- Posting too fast -> "Posting too frequently. Please try again shortly."
- Account suspended -> "Your account has been suspended. Please contact support."
- Blocked user -> "Unable to view this user" (must NOT reveal "they have blocked you" — avoids hostility)

### Finance / Banking
- Insufficient balance -> "Insufficient balance. Please verify and retry."
- OTP error -> "Verification code is invalid or expired"
- Daily limit reached -> "Daily transfer limit reached: NT$30,000"
- Account frozen -> "Account flagged. Please contact support: (02)XXXX-XXXX" (includes real support number)

## Your User Journeys

### E-commerce
- **happy-path-checkout**: login -> add to cart -> apply coupon -> checkout -> order confirmation page
- **coupon-redemption**: login -> claim coupon -> add eligible item -> apply at checkout -> verify discount amount
- **vip-upgrade**: account close to VIP threshold -> complete a qualifying order -> instant tier upgrade -> badge visible site-wide

### SaaS
- **onboarding**: signup -> verification email -> create first project -> invite first member -> finish welcome tour
- **plan-upgrade**: Free user hits cap -> upgrade prompt appears -> select plan -> pay -> caps lifted
- **member-revoke**: Owner removes member -> that member's active sessions should expire within N seconds

### Content / Social
- **first-post**: signup -> complete profile -> publish first post -> receive first like -> first-interaction milestone
- **moderation-flow**: user reports content -> enters moderation queue -> moderator decides -> both parties notified
- **block-and-unblock**: A blocks B -> mutual invisibility -> A unblocks -> visibility restored

### Finance / Banking
- **first-transfer**: signup -> KYC -> link bank account -> first transfer (with OTP) -> done
- **password-reset**: forgot password -> identity verification -> OTP -> set new password -> force all sessions to re-login
- **large-transfer**: initiate > NT$30,000 transfer -> trigger OTP + push -> dual confirmation -> done

## Your Technical Constraints

> This section tends to look similar across domains (environment / accounts /
> headers / random seeds) and is not broken out by archetype.

- **Test env URLs**: entry URLs for UAT / staging / local
- **Test users**: QA-only accounts + passwords (never use real user credentials)
- **Backend idempotency / tracing headers**: e.g. `X-Idempotency-Key` (UUID v4), `X-Request-Id`
- **Auth cookie / token**: e.g. `session_id` (HttpOnly), `csrf_token` (matching meta tag)
- **Deterministic seed**: e.g. `?qa_seed=<int>` pins backend randomness (discount calc / recommendations / push algorithms)
- **Feature flag overrides**: e.g. `?ff=newCheckout:on,oldCart:off` toggles flags in the QA environment
- **Locale**: e.g. `Accept-Language: en-US` is required to load the correct standard assertion strings
