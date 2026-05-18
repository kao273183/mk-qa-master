# mk-qa-master v0.7 — AI Visual Challenge Solver

**Status:** Draft v0.1 · **Author:** Jack Kao (kao273183) · **Last updated:** 2026-05-18 · **Target ship:** v0.7.0 within 2 weeks

---

## 1. Vision

> **The MCP family's first capability that genuinely depends on the AI client being multimodal — and uses that dependency as the architectural insight, not a workaround.**

mk-qa-master's runner abstraction (7 runners across web / mobile / API) handles everything the AI client *doesn't* need to see to decide. But CAPTCHA challenges are a class of problem where **the AI literally has to look at pixels**. Until now we deflected: the v0.6.3 knowledge layer recommends Tier 1 (backend bypass) for 90% of cases. v0.7.0 builds the Tier 3 escape hatch — the AI client's vision capability becomes the solver, MCP becomes the eyes and hands.

Two tools added (16 → 18 total):

- **`inspect_visual_challenge`** — detect the challenge, screenshot it, return structured tile metadata to the AI client
- **`solve_visual_challenge`** — accept the AI client's tile-selection decision, execute the click chain, submit

This is the **first MCP family feature where the AI client's intelligence is load-bearing, not optional**. The runner can't solve a CAPTCHA on its own; it depends on a multimodal client (Claude / Cursor / Gemini / GPT-4o) to interpret the image.

Chinese brand stays **AI 測試大師**. No rebrand.

---

## 2. Problem Statement

The v0.6.3 knowledge layer documents three tiers for CAPTCHA in automated testing:

1. **Tier 1** (recommended): bypass via reCAPTCHA test keys, feature flags, test-mode headers, or IP allowlist
2. **Tier 2**: degrade gracefully — mark as `external_dependency`, skip downstream assertions
3. **Tier 3**: AI visual judgment — *"planned in v0.7.0"*

Tier 1 covers most cases but leaves real gaps:

- QA engineers testing **client sites** with authorization but no backend access
- Staging environments that mirror production CAPTCHA exactly (no test keys configured)
- **Mobile webview** flows where the CAPTCHA shows up but you can't whitelist mobile carriers' IPs
- **Manual exploratory sessions** where the QA wants to power through a CAPTCHA once to inspect downstream

Today: these users abandon the automation, do it by hand, lose the audit trail.

v0.7.0 is the explicit-opt-in escape hatch for these 10% cases. **Default behavior unchanged** — CAPTCHA detection only fires when the user explicitly calls `inspect_visual_challenge`, never in `run_tests` by default.

---

## 3. Why now

- **The MCP family is now demonstrably multimodal-ready**. Anthropic shipped Claude Sonnet 4 vision; GPT-4o is standard at OpenAI; Gemini 2.5 ships native vision. Every major AI client mk-qa-master integrates with has vision. The architectural assumption is no longer speculative.
- **v0.6.3 forward-pointer is on the record**. The knowledge layer explicitly mentions `solve_visual_challenge`; shipping it within a release cycle keeps that promise concrete.
- **v0.6.0/0.6.1 proved the "AI doesn't need to know the runner" pattern**. Both Schemathesis and Newman work through the same 16-tool surface. v0.7.0 inverts: the AI client needs to *look*, not the runner. The pattern is symmetric — and adding tools (vs new runner) makes that symmetry visible.
- **No competitor MCP solves this**. The category is "OSS MCP that helps with visual CAPTCHA". There are commercial solvers (2Captcha, Anti-Captcha, CapMonster) but none MCP-native. Window is open.
- **Demo value is high**. A Claude session that visibly handles a CAPTCHA on screen is the kind of demo that lands on Show HN and r/ChatGPT. v0.7.0 is the most viral release of the v0.x line.

---

## 4. Competitive Positioning

| Tool | What they own | Where v0.7 differs |
|---|---|---|
| **2Captcha / Anti-Captcha / CapMonster** | Human-solver-as-a-service, $1-3 per 1k solves | We use the AI client *already in the developer's loop*. No new vendor, no per-solve fee. |
| **Capsolver / NopeCha** | ML model solvers | We don't ship a model — the AI client *is* the model. Cost = whatever Claude/Cursor already charge for vision. |
| **playwright-extra + stealth plugins** | Behavior-based bypass for reCAPTCHA v3 / Turnstile | Orthogonal. We solve image-grid CAPTCHA (v2 / hCaptcha); stealth plugins handle behavior-scoring CAPTCHA. Both layers coexist. |
| **No MCP competitor exists** | — | First-mover in the category |

### Defensible position

> The only **MCP-native CAPTCHA solver** that uses the AI client's own vision capability — no new vendor, no per-solve cost, no model maintenance. Solver-as-a-prompt rather than solver-as-a-service.

Five differentiators:

1. **MCP-native** — runs inside the AI client's tool surface
2. **No new dependencies** — no ML model bundled, no third-party API call
3. **Multimodal AI** as the solver — your existing Claude/Cursor subscription does the work
4. **Two-phase MCP design** — `inspect` returns; AI decides; `solve` executes. Clean atomic tool calls, no blocking RPC.
5. **Honest scope** — v2 / hCaptcha only. reCAPTCHA v3 / Cloudflare Turnstile explicitly out of scope (no challenge to look at).

---

## 5. Target Users

**Primary:** QA engineers running tests against client / partner sites under explicit authorization where backend bypass isn't possible.

**Secondary:** Indie devs / solo founders dogfooding their own product whose staging happens to mirror production CAPTCHA without test-key swap.

**Tertiary:** Manual testers using mk-qa-master as a power-user remote-control during exploratory sessions.

**Anti-personas:**

- Anyone trying to bypass CAPTCHA on third-party sites without authorization → **explicitly out of scope**. README disclaimer, error message on first use, hard-coded refusal patterns nope-out on common abuse signals.
- People expecting a one-click solution for reCAPTCHA v3 / Cloudflare Turnstile → **not solvable visually**. PRD §13 sets that expectation clearly.

---

## 6. MVP Scope (v0.7.0)

> **Decision boundary:** v0.7.0 ships **reCAPTCHA v2 image grid** support. hCaptcha follows in v0.7.1; v3 / Turnstile are explicit non-goals.

**In scope:**

- New tool: `inspect_visual_challenge` — detect, screenshot, return tile metadata
- New tool: `solve_visual_challenge` — accept tile selection, execute clicks, submit
- New env vars: `QA_VISUAL_CHALLENGE_TIMEOUT` (default 120s), `QA_VISUAL_CHALLENGE_CONSENT` (required `true` to enable, default `false`)
- iframe detection logic for reCAPTCHA v2 (well-known `iframe[title*="recaptcha"]` selector + frame URL match)
- Tile coordinate math: 3×3 / 4×4 grid normalization, viewport-relative click coordinates
- Screenshot capture via Playwright `page.locator(...).screenshot()` (base64-encoded for MCP TextContent return)
- Error shapes: `{error, retryable, hint}` — same as existing runners
- README updates: new section "AI Visual Challenge Solver" with consent gate explainer
- PRD section §25 ratified after this PRD review
- 4-6 smoke tests: tool registration, consent gate, dry-run inspection, click coordinate math
- CI: new `api-captcha` job (uses a Playwright route mock to serve a fake reCAPTCHA challenge — no live Google)

**Explicitly out of scope (deferred):**

- hCaptcha → v0.7.1 (same architecture, different iframe pattern)
- Audio CAPTCHA → not planned
- reCAPTCHA v3 / Cloudflare Turnstile → **not solvable visually**; out of scope permanently
- Cloudflare Bot Management challenges → not solvable visually
- Maestro / mobile native CAPTCHA → v0.8+ (different viewport model)
- Cassette recording of solved CAPTCHAs for replay → privacy / ethics concern, deferred

**v0.7.0 timeline target:** ship to PyPI within **2 weeks** of starting (~6-8 working hours of actual code given the established runner abstraction).

---

## 7. System Architecture

```
mk-qa-master/
├── src/mk_qa_master/
│   ├── tools/
│   │   ├── visual_challenge.py     # NEW — inspect + solve handlers
│   │   ├── analyzer.py             # existing
│   │   ├── generator.py            # existing
│   │   └── ...                     # existing
│   ├── server.py                   # register 2 new tools (16 → 18 total)
│   ├── config.py                   # QA_VISUAL_CHALLENGE_* env vars + consent gate
│   └── runners/pytest.py           # unchanged
├── tests/
│   └── test_visual_challenge.py    # NEW
└── docs/
    ├── prd-v0.7-visual-challenge.md  # this file
    └── walkthrough-visual-challenge.md  # NEW
```

**Key env vars (new):**

| Var | Required | Purpose |
|---|---|---|
| `QA_VISUAL_CHALLENGE_CONSENT` | YES (default `false`) | Must be set to `true` for the tools to function at all. Acts as an opt-in gate. First call without consent returns a structured error with the legal/ethical disclaimer. |
| `QA_VISUAL_CHALLENGE_TIMEOUT` | optional (default 120s) | Wall-clock budget for the whole inspect-solve cycle. Honors `QA_TIMEOUT_SECONDS` as a hard ceiling. |
| `QA_VISUAL_CHALLENGE_AUTHORIZED_DOMAINS` | optional | Comma-separated allowlist (e.g. `mysite.com,client-staging.example.com`). When set, refuses to operate on domains not in the list. **Recommended** for shared CI environments. |

---

## 8. Tool Surface

**Two new tools.** Total count goes 16 → **18**. The 16-tool claim in README / family-site needs updating.

| Tool | Purpose | Returns |
|---|---|---|
| `inspect_visual_challenge` | Detect any reCAPTCHA v2 iframe on the active page, screenshot the challenge tiles, return structured metadata including a per-tile coordinate grid and a session token. Idempotent within one page session. | `{ challenge_id, screenshot_base64, challenge_text, grid_layout: "3x3"|"4x4", tile_count: N, tiles: [{index, x, y, w, h}], expires_at, consent_required: bool }` |
| `solve_visual_challenge` | Apply the AI client's tile selection: click each indicated tile in order, click "Verify", await CAPTCHA completion (success or fail), return outcome. Single-shot — must be paired with the `challenge_id` returned by `inspect_visual_challenge`. | `{ status: "passed"|"failed"|"expired"|"error", token: str?, attempts_remaining: N, hint: str? }` |

**Why two tools, not one:**

A single composite tool would have to block waiting for the AI client's vision response, then click — but MCP tool calls are atomic and serialized. The two-tool design lets each call return immediately; the AI client decides between them. This matches every other tool in mk-qa-master (`run_tests`, `get_optimization_plan`, etc.) — each is a discrete decision boundary.

**Tool signatures:**

```python
inspect_visual_challenge(
    page_id: str | None = None,  # optional, defaults to active runner session
    selector: str | None = None,  # optional override; default = auto-detect known patterns
) -> {
    "challenge_id": "abc123-uuid",
    "screenshot_base64": "data:image/png;base64,...",
    "challenge_text": "Select all images with traffic lights",
    "grid_layout": "3x3",
    "tile_count": 9,
    "tiles": [
        {"index": 0, "viewport_x": 220, "viewport_y": 400, "w": 100, "h": 100},
        ...
    ],
    "expires_at": "2026-05-18T10:00:00Z",
    "fingerprint": "recaptcha-v2-image-3x3",
}

solve_visual_challenge(
    challenge_id: str,
    selected_tile_indices: list[int],
    confirm: bool = False,  # safety: must be set to True to actually click
) -> {
    "status": "passed" | "failed" | "expired" | "consent_required" | "error",
    "challenge_id": "abc123-uuid",
    "attempts_remaining": 2,
    "token": "g-recaptcha-response-token" | None,  # only on passed
    "hint": "Tiles 0, 4, 7 clicked. CAPTCHA verified. Resume your test." | None,
}
```

---

## 9. Data Model

**No new persistent state.** Each challenge_id is in-memory only (LRU cache, max 10 outstanding challenges per process, 5-minute TTL).

**No history archive** for solved CAPTCHAs. The decision to skip persistence is intentional:

- Privacy: archived screenshots could leak business-sensitive UI surrounding the CAPTCHA
- Audit risk: a CAPTCHA-bypass log could be subpoenaed in a TOS dispute
- Replay attack: storing solved tokens enables an attacker who reads the archive to reuse them

Telemetry: `inspect_visual_challenge` / `solve_visual_challenge` calls are logged to `telemetry.jsonl` (existing pattern) but **only with the boolean `passed: true|false`** — no challenge text, no tile selection, no screenshots.

---

## 10. Detection + execution flow

### Inspect phase
1. Use Playwright `page.frame_locator()` to find the reCAPTCHA challenge iframe
2. Probe known fingerprints:
   - `iframe[title="recaptcha challenge expires in two minutes"]` (English)
   - `iframe[src*="recaptcha/api2/bframe"]` (URL pattern)
   - hCaptcha equivalents (v0.7.1)
3. If no challenge present → return `{error: "no challenge", hint: "Page may not require CAPTCHA, or challenge has already been solved"}`
4. Screenshot the iframe via Playwright (cross-origin frames need a viewport-level screenshot crop)
5. Identify grid layout (count `td` cells in the tile table inside the iframe — reCAPTCHA v2 uses a stable DOM structure)
6. Read challenge instruction text from the iframe's `.rc-imageselect-desc` element
7. Compute per-tile viewport-relative coordinates (iframe offset + tile cell position)
8. Generate UUID `challenge_id`, cache `(challenge_id, page, frame_locator, tile_coords, expires_at)` in-memory
9. Return the structured payload

### Solve phase
1. Look up `challenge_id` in cache
2. Validate `confirm: True` is set (safety check — accidental call won't auto-click)
3. For each `selected_tile_indices[i]`:
   - Resolve to viewport coordinate via cached tile metadata
   - Issue Playwright `page.mouse.click(x, y)` with humanized timing (50-200ms jitter between clicks)
4. Wait for the "Verify" button to be enabled (reCAPTCHA disables it during selection)
5. Click "Verify" via known selector
6. Await result: success (token populates `g-recaptcha-response` textarea), failure (new challenge appears), or expiry (timeout)
7. Return outcome

### Consent gate
Both tools refuse to execute when `QA_VISUAL_CHALLENGE_CONSENT != "true"`. Error response includes the full legal/ethical disclaimer text (see §13) so the AI client surfaces it to the user.

When `QA_VISUAL_CHALLENGE_AUTHORIZED_DOMAINS` is set, the tools check `page.url`'s domain against the allowlist on every call. Failure → `{error: "unauthorized_domain", hint: "Domain X is not in QA_VISUAL_CHALLENGE_AUTHORIZED_DOMAINS"}`.

---

## 11. Integration with existing surface

**No MCP-to-MCP RPC.** Same as the rest of mk-qa-master.

**No automatic CAPTCHA handling in `run_tests`.** Explicit opt-in only. A `run_tests` session that hits a CAPTCHA fails normally with an `external_dependency` classification (per v0.6.3 Tier 2). The user has to explicitly call `inspect_visual_challenge` if they want to escalate to Tier 3.

Canonical chain in a Claude / Cursor session:

```
1.  mk-qa-master.run_tests()
    → 1 test failed, classified `external_dependency` (CAPTCHA detected)
    → optimization-plan.md notes: "use inspect_visual_challenge to attempt resolution"

2.  user: "Try solving it"

3.  mk-qa-master.inspect_visual_challenge(page_id=<from-failed-run>)
    → returns screenshot + tile grid + challenge_id

4.  AI client examines the screenshot
    → "Tiles 0, 4, 7 contain traffic lights"

5.  mk-qa-master.solve_visual_challenge(
        challenge_id="abc-123",
        selected_tile_indices=[0, 4, 7],
        confirm=True,
    )
    → status: "passed", token: "..."

6.  mk-qa-master.run_failed()
    → previously-blocked tests now run, pass downstream
```

The optimizer surfaces CAPTCHA-classified failures with an explicit pointer at this chain.

---

## 12. Self-reinforcement

The CAPTCHA solver participates in mk-qa-master's existing optimizer pipeline:

- **Suite quality lens**: a test that consistently fails at the CAPTCHA boundary gets classified `external_dependency` (already exists from v0.6.x). The optimizer notes "try inspect_visual_challenge" in the plan.
- **MCP usability lens**: telemetry tracks (`inspect_visual_challenge → solve_visual_challenge`) as a known pair, surfacing common chains. If users call `inspect` twice in a row (a sign the first AI attempt failed), the lens flags it.
- **AI effectiveness lens**: per-call passed/failed boolean is logged. Aggregate success rate visible via `get_telemetry`. Below 50% suggests the AI client isn't doing well at the challenge — actionable signal.

**No new self-reinforcement infra needed** — reuses telemetry + optimizer.

---

## 13. Non-functional Requirements

| Concern | Requirement |
|---|---|
| **Consent gate** | `QA_VISUAL_CHALLENGE_CONSENT=true` required. Default `false`. Clear error message on first call without consent. |
| **Domain allowlist** | `QA_VISUAL_CHALLENGE_AUTHORIZED_DOMAINS` recommended; tools warn (not block) if unset. |
| **Privacy** | No screenshot retention. Telemetry stores boolean outcome only. No challenge text logged. |
| **Performance** | inspect: < 3s. solve: < 30s including verify wait. Hard ceiling: `QA_VISUAL_CHALLENGE_TIMEOUT` (default 120s). |
| **Compatibility** | Python 3.10+, Playwright ≥ 1.40 (web only — Maestro deferred to v0.8). |
| **Errors** | Structured `{error, retryable, hint}` shape, mirrors all other runners. |
| **Legal disclaimer text** | Embedded in the error message returned on first unconfirmed call. Includes: "Use only on sites you own or have explicit authorization to test. Third-party use may violate TOS / CFAA (US) / equivalent in your jurisdiction." |
| **Ethical hard-stops** | Domain blacklist of known abuse targets (Cloudflare site list, Google login domains, common login portals not associated with QA test fixtures). Refuse to operate on these regardless of consent flag. |

---

## 14. Roadmap

| Milestone | Scope | Target |
|---|---|---|
| **v0.7.0 (MVP)** | reCAPTCHA v2 image-grid support, two tools, consent gate, domain allowlist, CI smoke job with mock CAPTCHA | 2 weeks |
| **v0.7.1** | hCaptcha (same iframe pattern, different selectors) | +1 week |
| **v0.7.2** | "Best guess" auto-click on simple challenges where the AI is highly confident (skip the inspect/solve round-trip) | +2 weeks |
| **v0.8** | Mobile webview support (Maestro can drive a webview, surface the visual challenge there) | Q4 2026 |
| **v1.0** | Production-ready with comprehensive consent/auth flow, audit log opt-in, telemetry dashboard | End Q1 2027 |

**Realistic calendar:**
- v0.7.0 ship: ~2026-05-31
- v0.7.1 ship: ~2026-06-07
- v0.7.2 ship: ~2026-06-21

---

## 15. Open Questions / Risks

| # | Question | Mitigation |
|---|---|---|
| Q1 | What if the AI client misidentifies tiles consistently (< 50% success)? | The Verify button on reCAPTCHA only locks out after 3 failures. Tool returns `attempts_remaining` so the AI can retry with refined selection. |
| Q2 | How to handle reCAPTCHA's "dynamic challenge" (new images load after each click)? | reCAPTCHA may return a new sub-challenge after the user clicks Verify but before passing. We surface this as a fresh `inspect_visual_challenge` opportunity (new `challenge_id`). |
| Q3 | Cross-origin iframe screenshots can be blocked in some browsers | Playwright handles this with elevated permissions in test mode. Document the requirement explicitly. |
| Q4 | Should the consent gate accept JIRA / GitHub issue ID as proof of authorization? | v0.7.x: no — keep the gate simple (env var only). v0.8: could integrate with mk-spec-master for issue-grounded authorization. |
| R1 | Google detects automation + bans the IP / session | Documented loudly. Tools include a "this may be detected" warning in the response payload. Mitigation: use stealth plugins (playwright-extra), residential proxies, real browser profiles. |
| R2 | TOS / legal exposure for users running against third-party sites | Hard-stops on known third-party login domains. Disclaimer in error message. README has a dedicated "Acceptable Use" section. License unchanged (MIT). |
| R3 | AI client's vision performance varies wildly across Claude / GPT-4o / Gemini / Cursor | Document expected success rate per client based on internal benchmarks (when available). |
| R4 | Solving CAPTCHAs becomes the "thing people use mk-qa-master for" — distracts from core QA narrative | Keep the feature opt-in by env var. Position as Tier 3 escape hatch, not headline capability. README leads with web/mobile/API testing; CAPTCHA solver is below the fold. |

---

## 16. Success Metrics

**Adoption (3 months from v0.7.0):**

- 10+ users opt-in via `QA_VISUAL_CHALLENGE_CONSENT=true` (tracked via telemetry, anonymously)
- 3 unsolicited Issues / PRs about visual challenge handling
- Mentioned in 1 dev-tools or AI-tools blog post as a noteworthy capability
- Show HN post lands above 100 upvotes on launch day

**Quality (any time):**

- 100% backwards-compatible — existing pytest / Jest / Cypress / Go / Maestro / Schemathesis / Newman users see no regression
- CI smoke job passes on every push
- Glama coherence score doesn't drop
- AI success rate ≥ 60% on the mock CAPTCHA used in CI

**Family effect (6 months):**

- v0.7.1 (hCaptcha) ships if v0.7.0 has any uptake
- Dev.to launch post leads with "Claude can solve CAPTCHAs through this MCP" hook
- Family-site qa-master deep page promotes the visual challenge capability

---

## 17. Open Source Strategy

- License: MIT (mirror existing mk-qa-master)
- Repo: same repo, branch `feat/visual-challenge-v0.7` → PR → squash merge
- CI from day 1: extend `ci.yml` with `api-captcha` job using a Playwright route-mock fixture (no live Google network calls)
- No new optional dep — uses Playwright (already a base dep)
- Blog post on launch: dedicated post explaining the consent model + when to use Tier 1 vs Tier 3
- Show HN at v0.7.0 ship: `Show HN: mk-qa-master v0.7 — Claude solves reCAPTCHA through this MCP, no new vendor`

---

## 18. Naming

| Surface | Value |
|---|---|
| Feature name (EN) | AI Visual Challenge Solver |
| Feature name (中) | AI 視覺挑戰解析 |
| Tool 1 id | `inspect_visual_challenge` |
| Tool 2 id | `solve_visual_challenge` |
| Env var prefix | `QA_VISUAL_CHALLENGE_*` |
| Version target | v0.7.0 |
| Tagline in README | "When backend bypass isn't an option: Claude looks at the CAPTCHA, mk-qa-master does the clicks." |

**Why not `solve_captcha` / `inspect_captcha`:**

The pattern (screenshot + tile selection + click chain) generalizes beyond CAPTCHA — any visual decision the AI client needs to make about an iframe can use the same primitives. Future use cases: "select the cell containing the date you want", "click the company logo in this grid". The naming reflects the generalized primitive, not the CAPTCHA-specific application.

---

## 19. Walkthrough Example

A solo dev running mk-qa-master against their client's staging site, with written authorization, where backend bypass isn't available.

**Step 1 — Initial setup:**

```bash
export QA_RUNNER=pytest
export QA_PROJECT_ROOT=/path/to/test-project
export QA_VISUAL_CHALLENGE_CONSENT=true
export QA_VISUAL_CHALLENGE_AUTHORIZED_DOMAINS=client-staging.example.com
```

**Step 2 — Run tests, hit CAPTCHA:**

```
mk-qa-master.run_tests()
  → tests/test_signup.py::test_create_account FAILED
  → classified: external_dependency (CAPTCHA detected at signup form)
  → optimization-plan.md hint: "use inspect_visual_challenge to attempt Tier 3 resolution"
```

**Step 3 — Inspect:**

```
mk-qa-master.inspect_visual_challenge(page_id="active")
  → {
      challenge_id: "ed1f7-...",
      screenshot_base64: "...",
      challenge_text: "Select all images with traffic lights",
      grid_layout: "3x3",
      tile_count: 9,
      tiles: [...],
      expires_at: "2026-05-31T14:32:00Z",
    }
```

**Step 4 — AI examines screenshot:**

User talking to Claude: "Look at the screenshot. Which tiles have traffic lights?"

Claude (via vision): "Tiles 0, 4, and 7 contain traffic lights."

**Step 5 — Solve:**

```
mk-qa-master.solve_visual_challenge(
    challenge_id="ed1f7-...",
    selected_tile_indices=[0, 4, 7],
    confirm=True,
)
  → {
      status: "passed",
      token: "g-recaptcha-response-...",
      attempts_remaining: 2,
      hint: "CAPTCHA verified. Resume your test.",
    }
```

**Step 6 — Resume:**

```
mk-qa-master.run_failed()
  → tests/test_signup.py::test_create_account PASSED
  → downstream assertions all pass
```

**Total time:** ~30 seconds end-to-end. **Without v0.7.0:** test stalls indefinitely, manual intervention required.

**This is the demo video.** Show HN screenshot: a Claude / Cursor session that visibly resolves a CAPTCHA inline.

---

## 20. Decision Required Before Coding

1. **Tool granularity** — confirm two tools (inspect + solve), or fold into one with internal blocking call? (Recommend: two tools.)
2. **Consent gate** — env var only, or also require per-call confirmation? (Recommend: env var gate + `confirm=True` per `solve_visual_challenge` call.)
3. **Domain allowlist behavior** — block on mismatch (recommended), or warn-only? (Recommend: block when set; warn when unset.)
4. **CI fixture** — mock CAPTCHA via Playwright route or use a known stable reCAPTCHA test endpoint? (Recommend: route-mock — fully self-contained, no Google dependency.)
5. **Public PRD timing** — Day 1 (mirror prior PRDs) or hold until v0.7.0 ships? (Recommend: Day 1.)

---

## 21. Decisions ratified

Locked 2026-05-18 after PRD review:

1. **Tool granularity** — **two tools**: `inspect_visual_challenge` (surfaces the challenge to the AI client) + `solve_visual_challenge` (accepts the AI client's tile selection and executes). MCP tool calls are atomic; a single composite tool would have to block on the AI client's vision response, which violates the protocol semantics.
2. **Consent gate** — **env var AND per-call confirmation**. `QA_VISUAL_CHALLENGE_CONSENT=true` is required at the server level (default `false`); on top of that, every `solve_visual_challenge` call requires `confirm=True` as a safety latch against accidental invocation.
3. **Domain allowlist** — **block on mismatch when set, warn-only when unset**. `QA_VISUAL_CHALLENGE_AUTHORIZED_DOMAINS` is recommended for shared CI / multi-tenant environments; when present, the tool refuses to operate on any domain not listed. When the variable is unset, the tool emits a warning in the response but proceeds (single-user dev ergonomics).
4. **CI fixture** — **Playwright route mock** of a fake reCAPTCHA challenge served from `examples/sample_captcha_fixture/`. Fully self-contained: no live Google calls, no network dependency, deterministic across runs. Real reCAPTCHA testing is user-side.
5. **PRD public timing** — **Day 1** (this commit). Mirrors the v0.6 / mk-plan-master pattern of build-in-public PRD-first development. The CAPTCHA section in v0.6.3 already forward-points at this work; hiding the PRD would be inconsistent.

---

*End of PRD v0.1 for mk-qa-master v0.7. Discussion in mk-qa-master Issues once a draft is opened.*
