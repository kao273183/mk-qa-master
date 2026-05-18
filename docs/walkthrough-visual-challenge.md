# Walkthrough — AI Visual Challenge Solver

mk-qa-master v0.7.0 ships the first capability in the family where the
AI client's vision is **load-bearing, not optional**. The runner
detects a reCAPTCHA v2 image-grid challenge and screenshots it for the
AI client; the AI client (Claude / Cursor / Gemini / GPT-4o, all
multimodal) uses its own vision to identify which tiles to click; the
runner executes the click chain.

This is the **Tier 3** escape hatch in the CAPTCHA strategy (see
`get_qa_context section="CAPTCHA"`). Reach for it only when Tier 1
(backend bypass via reCAPTCHA test keys, feature flags, IP allowlist)
and Tier 2 (degrade-and-skip) aren't available.

Two MCP tools added in v0.7.0:

- `inspect_visual_challenge` — detect, screenshot, return tile metadata
- `solve_visual_challenge` — accept AI tile selection, execute clicks,
  submit

Total MCP tool count goes 16 → 18.

---

## Prerequisites

```bash
pip install 'mk-qa-master>=0.7.0'
playwright install chromium
```

The visual challenge solver reuses the Playwright dep that already
backs the pytest-playwright runner — no new MCP-level dependencies.

You need a **multimodal AI client** to do the actual judging. Claude
Desktop / Cursor / Gemini CLI / Codex CLI all qualify. A text-only
client can call the tools but won't see the screenshot, so it can't
pick tiles.

## The consent gate

The solver does nothing without explicit opt-in. Two env vars matter:

```jsonc
{
  "mcpServers": {
    "mk-qa-master": {
      "command": "uvx",
      "args": ["mk-qa-master"],
      "env": {
        "QA_RUNNER": "pytest",
        "QA_PROJECT_ROOT": "/path/to/your-test-project",
        "QA_VISUAL_CHALLENGE_CONSENT": "true",
        "QA_VISUAL_CHALLENGE_AUTHORIZED_DOMAINS": "client-staging.example.com"
      }
    }
  }
}
```

| Variable | Required | Default | What it does |
|---|---|---|---|
| `QA_VISUAL_CHALLENGE_CONSENT` | yes | `false` | Server-level master switch. Without it, both tools return a structured `consent_required` error carrying the full legal disclaimer. |
| `QA_VISUAL_CHALLENGE_AUTHORIZED_DOMAINS` | no (recommended) | — | Comma-separated allowlist of domains the tool may operate on. Strict-block when set; warn-only when unset. |
| `QA_VISUAL_CHALLENGE_TIMEOUT` | no | `120` | Wall-clock budget (seconds) for the inspect→solve cycle. |

On top of these, every `solve_visual_challenge` call requires
`confirm=true` as a per-call safety latch. A tool call with
`confirm=false` returns `confirm_required` and clicks nothing.

> **Acceptable Use** — this tool is intended for QA testing on sites
> you own, client sites where you have written authorization, or test
> environments where Tier 1 bypass isn't available. **Do not use it on
> third-party sites you don't own.** Solving CAPTCHAs without
> authorization may violate the Computer Fraud and Abuse Act (US),
> GDPR (EU), or equivalent jurisdictions. The user is solely
> responsible for legal compliance.

## Hard-stop domains

Regardless of consent or allowlist, the tools refuse to operate on:

- `accounts.google.com`
- `login.microsoftonline.com`
- `id.apple.com` / `appleid.apple.com`
- `facebook.com` / `www.facebook.com`
- `login.live.com`
- `login.yahoo.com`
- `twitter.com/login` / `x.com/login`

No legitimate QA scenario justifies a CAPTCHA solver against someone
else's identity provider.

---

## Session transcript

What follows is a real Claude session against a fictional client site
under written authorization (`client-staging.example.com`). The MCP
tool calls are explicit so you can map them onto your own client; in
practice the user just types natural language.

### 1. Run the suite, hit a CAPTCHA wall

> **You**: Run the signup tests.

Claude calls `run_tests(filter="signup")`. One test fails — classified
as `external_dependency`:

```json
{
  "exit_code": 1,
  "stdout_tail": "... 1 failed in 18.2s — tests/test_signup.py::test_create_account",
  "...": "..."
}
```

`get_failure_details` shows the underlying cause: the signup form
loaded a reCAPTCHA v2 challenge and the existing test couldn't get
past it. The `optimization-plan.md` hint surfaces:

```markdown
### 1. 🟡 MEDIUM — external_dependency
- **Target**: tests/test_signup.py::test_create_account
- **Evidence**: page hit reCAPTCHA v2 challenge at /signup
- **Suggestion**: Tier 1 — configure reCAPTCHA test keys in staging.
  If unavailable, escalate to Tier 3: `inspect_visual_challenge`
  → AI vision → `solve_visual_challenge`.
```

### 2. Escalate to Tier 3

> **You**: Backend bypass isn't an option here. Try solving it.

Claude calls `inspect_visual_challenge()`. The tool checks the consent
gate (`QA_VISUAL_CHALLENGE_CONSENT=true` ✓), domain allowlist
(`client-staging.example.com` ✓), and the hard-stop list (no match
✓), then probes the page for a reCAPTCHA iframe.

Response:

```json
{
  "challenge_id": "ed1f7a9b3c4d",
  "screenshot_base64": "data:image/png;base64,iVBORw0KGgo...",
  "challenge_text": "Select all images with traffic lights",
  "grid_layout": "3x3",
  "tile_count": 9,
  "tiles": [
    {"index": 0, "viewport_x": 220, "viewport_y": 400, "w": 100, "h": 100},
    {"index": 1, "viewport_x": 320, "viewport_y": 400, "w": 100, "h": 100},
    "...": "..."
  ],
  "expires_at": "2026-05-18T10:32:00Z",
  "fingerprint": "recaptcha-v2-image-3x3"
}
```

### 3. AI examines the screenshot

The AI client renders the `screenshot_base64` payload inline and looks
at the 3x3 grid. (Claude Desktop / Cursor render base64 images
automatically; the underlying vision model — Claude Sonnet 4 in this
example — does the actual judging.)

> **Claude**: Tiles 0, 4, and 7 contain traffic lights. The center
> tile is a clear daytime signal; the top-left has a stoplight pole
> visible; the bottom-middle shows a horizontal three-bulb fixture.

### 4. Execute the click chain

> **You**: Looks right. Submit it.

Claude calls:

```python
mk-qa-master.solve_visual_challenge(
    challenge_id="ed1f7a9b3c4d",
    selected_tile_indices=[0, 4, 7],
    confirm=True,
)
```

The runner clicks each tile by viewport coordinate (with humanized
100ms jitter between clicks so the dispatcher doesn't tear the grid
state), clicks the Verify button inside the iframe, then polls the
page for the `g-recaptcha-response` token.

Response:

```json
{
  "status": "passed",
  "challenge_id": "ed1f7a9b3c4d",
  "attempts_remaining": 2,
  "token": "03AGdBq25-...long-jwt...",
  "hint": "Tiles [0, 4, 7] clicked. CAPTCHA verified. Resume your test."
}
```

### 5. Resume the test

> **You**: Re-run only the failure.

Claude calls `run_failed`. The CAPTCHA-blocked test now sails past
the gate; downstream assertions all pass:

```json
{
  "exit_code": 0,
  "stdout_tail": "1 passed in 4.1s — tests/test_signup.py::test_create_account",
  "total": 1, "passed": 1, "failed": 0
}
```

Total wall time, end-to-end: **~30 seconds**. Without v0.7, this test
stalls indefinitely waiting for a human to click the grid.

---

## The two-phase dance, visible

The key architectural decision in v0.7 (ratified in PRD §21 #1) is
that this is **two atomic tools**, not one composite tool:

```
inspect_visual_challenge → return immediately with the screenshot
        │
        ▼
[AI client's vision model decides which tiles to click]
        │
        ▼
solve_visual_challenge → accept the decision, click, return outcome
```

MCP tool calls are atomic and serialized. A single composite tool
would have to block waiting for the AI client's vision response, which
violates the protocol. Splitting it into two lets each call return
immediately and the AI client orchestrate the chain — same shape as
`analyze_url → generate_test → run_tests` or any other multi-step
chain in this MCP.

## What can go wrong

- **`status: "failed"`** — the Verify button was clicked but the
  token didn't appear within the budget. Usually means the AI picked
  the wrong tiles. Call `inspect_visual_challenge` again to get a
  fresh challenge_id (reCAPTCHA often surfaces a new sub-challenge
  after a miss) and let the AI retry. You get 3 attempts total before
  reCAPTCHA locks the session.

- **`status: "expired"`** — more than 5 minutes elapsed between
  inspect and solve. Re-inspect.

- **`status: "challenge_not_found"`** — the challenge_id you passed
  isn't in the cache. Either it expired, the LRU evicted it (10
  outstanding max per process), or it was never issued. Re-inspect.

- **`error: "consent_required"`** — you forgot
  `QA_VISUAL_CHALLENGE_CONSENT=true`. The hint field contains the
  full legal disclaimer.

- **`error: "unauthorized_domain"`** — you set
  `QA_VISUAL_CHALLENGE_AUTHORIZED_DOMAINS` and the page's domain
  isn't on the list. Either fix the allowlist or stop targeting that
  domain.

- **`error: "forbidden_domain"`** — you tried to point this at
  `accounts.google.com` (or another hard-stop). This refusal is
  permanent — no env var unlocks it.

- **`error: "no_challenge_present"`** — the page didn't render a
  reCAPTCHA iframe at the time of the call. Either the gate isn't
  there, you already solved it, or your selector override missed.

## Privacy and telemetry

The 5-minute LRU cache that holds outstanding challenges is **memory
only**. Nothing about the challenge — not the screenshot, not the
prompt text, not the tile selection — is ever persisted. Telemetry
logs the boolean `passed: true|false` outcome only, mirroring the
ratification in PRD §9. This is by design:

- **Archived screenshots** could leak business-sensitive UI
  surrounding the CAPTCHA.
- **A CAPTCHA-bypass log** could be subpoenaed in a TOS dispute.
- **Storing solved tokens** would enable a replay attack against
  whichever site the operator just solved against.

The aggregate pass-rate is the only signal `get_optimization_plan`
surfaces from this layer — and that's enough to detect when an AI
client's vision model is performing poorly (e.g. below 50%) and
suggest a tier-down to Tier 1 bypass.

## What this isn't

- **Not a hCaptcha solver** — that's v0.7.1, same architecture,
  different iframe selectors.
- **Not a reCAPTCHA v3 / Cloudflare Turnstile solver** — those don't
  surface a visible challenge to inspect. They score behavior, not
  pixels. Out of scope permanently.
- **Not a CAPTCHA-bypass-as-a-service** — there's no remote API call,
  no third-party model, no per-solve fee. The AI client's existing
  vision capability is the entire solver. Cost = whatever Claude /
  Cursor / Gemini already charge for vision tokens.
- **Not enabled by default in `run_tests`** — explicit opt-in only. A
  `run_tests` session that hits a CAPTCHA fails normally with an
  `external_dependency` classification; the user has to **deliberately**
  call `inspect_visual_challenge` to escalate to Tier 3.

## Where to go from here

- **Wire it into your client config** with the env vars above. Start
  with a single allowlisted staging domain so accidental misuse
  surfaces as `unauthorized_domain` rather than a successful solve
  against the wrong site.
- **Try the bundled fixture** at
  [`examples/sample_captcha_fixture/`](../examples/sample_captcha_fixture/) —
  a self-contained reCAPTCHA-shaped fixture that lets you exercise
  the tool chain without ever calling Google.
- **Read the PRD** at
  [`docs/prd-v0.7-visual-challenge.md`](prd-v0.7-visual-challenge.md)
  for the full design notes — §10 covers the detection + execution
  flow, §13 codifies the consent + privacy requirements, §21
  documents the ratified decisions.
- **Watch the success-rate telemetry** via `get_telemetry`. If your
  AI client's vision is under 50% on your specific challenges, that's
  a strong signal to invest more in Tier 1 instead.

Tier 3 is the escape hatch. Tier 1 is still the right answer 90% of
the time. v0.7's job is to make the remaining 10% solvable.
