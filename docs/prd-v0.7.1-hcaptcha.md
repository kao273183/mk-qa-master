# mk-qa-master v0.7.1 — hCaptcha Support (mini-PRD)

**Status:** Draft v0.1 · **Author:** Jack Kao (kao273183) · **Last updated:** 2026-05-23 · **Target ship:** v0.7.1 within 1 week of go-decision

This is a **mini-PRD**, not the full 21-section format. v0.7.1 extends v0.7.0's architecture to a second CAPTCHA vendor (hCaptcha) — same tools, same consent model, same telemetry. Most v0.7.0 PRD decisions carry over verbatim.

Refer to [`docs/prd-v0.7-visual-challenge.md`](prd-v0.7-visual-challenge.md) for the full architectural rationale.

---

## 1. Vision

> **Extend the AI Visual Challenge Solver to hCaptcha. Same architecture. Different fingerprint.**

v0.7.0 ships reCAPTCHA v2 image-grid solving. hCaptcha is the privacy-focused alternative — different vendor, same image-grid challenge pattern, same multimodal AI use case. v0.7.1 adds hCaptcha detection + execution to the existing `inspect_visual_challenge` / `solve_visual_challenge` tools.

**No new MCP tools. Tool count stays at 18.** This is a fingerprint + selector extension, not a feature addition.

---

## 2. Problem Statement

User-side: many sites on the privacy-conscious end of the market (Cloudflare partners, indie SaaS, EU-heavy products) use hCaptcha instead of reCAPTCHA. v0.7.0 detects no challenge on these sites and returns `{error: "no_challenge_present"}` — leaving the user stuck without a Tier 3 escape.

Architecturally: hCaptcha's iframe structure mirrors reCAPTCHA v2's enough that 90% of v0.7.0 code applies unchanged. Adding hCaptcha is filling in a fingerprint table + adjusting two selectors, not designing a new flow.

---

## 3. MVP Scope (v0.7.1)

**In scope:**
- hCaptcha iframe detection alongside reCAPTCHA v2 (both vendors auto-detected via fingerprint priority)
- hCaptcha-specific selectors for challenge text, tile grid, Verify button
- hCaptcha response token extraction (`h-captcha-response` textarea, vs reCAPTCHA's `g-recaptcha-response`)
- Updated `fingerprint` field in `inspect_visual_challenge` response: `recaptcha-v2-image-3x3` | `hcaptcha-image-3x3` | `hcaptcha-image-4x4`
- 5-6 new unit tests covering hCaptcha-specific code paths
- New CI fixture: `examples/sample_hcaptcha_fixture/iframe.html` (mock hCaptcha alongside existing reCAPTCHA mock)
- README + walkthrough updates noting hCaptcha support

**Explicitly out of scope (carried from v0.7.0 §6):**
- reCAPTCHA v3 — no visible challenge
- Cloudflare Turnstile — pure behavior scoring, no challenge
- Audio CAPTCHA — accessibility fallback, low usage
- hCaptcha Enterprise behavior-mode — same reason as v3
- Maestro / mobile webview hCaptcha — v0.8 territory

**v0.7.1 timeline:** ~4-6 working hours of code, plus tests + CI + docs + PR + release.

---

## 4. Architecture Differences from v0.7.0

### Fingerprint table

```python
_FINGERPRINTS = [
    {
        "id": "recaptcha-v2-image",
        "iframe_selectors": [
            'iframe[title*="recaptcha challenge"]',
            'iframe[src*="recaptcha/api2/bframe"]',
        ],
        "challenge_text_selector": ".rc-imageselect-desc",
        "tile_table_selector": ".rc-imageselect-table",
        "verify_button_selector": "#recaptcha-verify-button",
        "response_token_selector": 'textarea[name="g-recaptcha-response"]',
    },
    {
        "id": "hcaptcha-image",
        "iframe_selectors": [
            'iframe[src*="hcaptcha.com"]',
            'iframe[title*="hCaptcha"]',
            'iframe[title*="Main content of the hCaptcha"]',
        ],
        "challenge_text_selector": ".prompt-text",
        "tile_table_selector": ".task-grid",
        "verify_button_selector": ".button-submit",
        "response_token_selector": 'textarea[name="h-captcha-response"]',
    },
]
```

Detection iterates the table in order; first match wins. reCAPTCHA priority preserves v0.7.0 behavior for existing users.

### Tile grid layout

| Vendor | Common layouts | Notes |
|---|---|---|
| reCAPTCHA v2 | 3×3, 4×4 (dynamic) | "Select all images with X" |
| hCaptcha | 3×3 (image-select), 4×4 (rare) | "Please click each image containing X" |

Both layouts use `<td>` / equivalent cell elements; the v0.7.0 cell-counting logic transfers without change.

### Response token

After Verify success, the page sets a textarea value:
- reCAPTCHA v2: `textarea[name="g-recaptcha-response"]`
- hCaptcha: `textarea[name="h-captcha-response"]`

`solve_visual_challenge` should return the token corresponding to the active fingerprint.

---

## 5. Tool Surface

**Zero new MCP tools.** Existing surface unchanged:

- `inspect_visual_challenge` — now returns `fingerprint: "hcaptcha-image-3x3"` when hCaptcha detected (was `recaptcha-v2-image-3x3` only). All other response fields identical.
- `solve_visual_challenge` — returns hCaptcha token (`h-captcha-response`) when active fingerprint is hCaptcha. All other response fields identical.

AI clients consuming these tools need **no code change** to use hCaptcha support. The `fingerprint` field is informational; the click + verify flow is fingerprint-agnostic.

---

## 6. Consent / Safety

All v0.7.0 §13 NFR carries verbatim:

- `QA_VISUAL_CHALLENGE_CONSENT=true` required (default false)
- `QA_VISUAL_CHALLENGE_AUTHORIZED_DOMAINS` enforced when set
- Same hard-stop blacklist (third-party login providers refused regardless of consent)
- Telemetry: boolean outcome only, no screenshots / challenge text / tile selection

**One addition:** the hard-stop blacklist gets reviewed for hCaptcha-protected domains commonly abused for credential stuffing. Initial additions: `discord.com` (uses hCaptcha for registration), `epicgames.com`, `mailbox.org`.

---

## 7. Tests

Add to `tests/test_visual_challenge.py`:

```
test_inspect_detects_hcaptcha_iframe         # hCaptcha selector matches
test_solve_returns_hcaptcha_token            # h-captcha-response read correctly
test_fingerprint_field_reports_vendor        # "hcaptcha-image-3x3" vs reCAPTCHA
test_recaptcha_takes_priority_when_both     # edge case: both iframes present
test_hcaptcha_4x4_grid_layout                # rare but exists
```

Add to `tests/test_smoke.py`:

```
test_visual_challenge_fingerprint_table_includes_hcaptcha
```

New CI fixture: `examples/sample_hcaptcha_fixture/iframe.html` — self-contained 3×3 grid with `.prompt-text` + `.task-grid` + `.button-submit` + `textarea[name="h-captcha-response"]`. Used by new `api-hcaptcha` CI job (parallel to existing `api-captcha` reCAPTCHA job).

---

## 8. Implementation Plan

| Step | Time | What |
|---|---|---|
| 1 | 30 min | Refactor `_detect_recaptcha` → `_detect_visual_challenge` (vendor-neutral); introduce fingerprint table |
| 2 | 1 hr | hCaptcha fingerprint entries + selector resolution logic |
| 3 | 30 min | Response token extraction by vendor |
| 4 | 1 hr | Sample hCaptcha fixture HTML + Playwright route mock |
| 5 | 1.5 hr | New unit tests (5-6 cases) + new CI job |
| 6 | 30 min | README + walkthrough updates (mention hCaptcha alongside reCAPTCHA) |
| 7 | 30 min | PRD v0.7 §22 ratification append; bump pyproject.toml; PR |
| **Total** | **~5 hr** | Plus PR / CI / release time |

Subagent can complete steps 1-6 in one delegation. Step 7 is local + PR work.

---

## 9. Roadmap Context

This PRD lives at `docs/prd-v0.7.1-hcaptcha.md` to keep v0.7's big PRD focused. After ratification, append `## 22. v0.7.1 ratified` to the v0.7 PRD with a 5-line cross-reference to this document.

Subsequent visual-challenge work:
- v0.7.2 — best-guess auto-solve (still under PRD v0.7 §14, may or may not ship)
- v0.8.0 — Mobile / Maestro webview support (separate mini-PRD)
- v0.9.0 — Pact + `analyze_api` (separate PRD, returns to API testing arc)

---

## 10. Decisions Required Before Coding

1. **Fingerprint priority** — reCAPTCHA wins when both iframes present? Or hCaptcha wins (more privacy-conscious sites prefer it)? Recommend: **reCAPTCHA first** (preserves v0.7.0 user behavior, no surprise).
2. **`fingerprint` field naming** — `recaptcha-v2-image-3x3` / `hcaptcha-image-3x3` (vendor-prefixed)? Or just `image-3x3` (layout-only)? Recommend: **vendor-prefixed** (debugging clarity worth the slightly longer string).
3. **hard-stop blacklist additions** — which hCaptcha-protected domains to refuse by default? Recommend: **discord.com, epicgames.com, mailbox.org**. Conservative; user can override via allowlist if they own the site.
4. **CI fixture sharing** — bundle hCaptcha mock under existing `examples/sample_captcha_fixture/` or new `examples/sample_hcaptcha_fixture/`? Recommend: **new directory** (clarity over deduplication).
5. **Token-field naming in `solve_visual_challenge` response** — keep single `token` field (current) or split to `recaptcha_token` / `hcaptcha_token`? Recommend: **single `token`** + `fingerprint` field tells you which vendor.

---

## 11. Decisions Ratified

Locked 2026-05-23:

1. **reCAPTCHA wins when both iframes present** — preserves v0.7.0 user behavior; no surprise for existing callers.
2. **`fingerprint` field is vendor-prefixed** — `recaptcha-v2-image-3x3` / `hcaptcha-image-3x3`. Slightly longer string buys debug clarity.
3. **Hard-stop blacklist additions**: `discord.com`, `epicgames.com`, `mailbox.org`. Conservative; users with legitimate need can override via `QA_VISUAL_CHALLENGE_AUTHORIZED_DOMAINS`.
4. **CI fixture in a new directory** — `examples/sample_hcaptcha_fixture/`. Clarity beats deduplication.
5. **Single `token` field in `solve_visual_challenge` response** — the `fingerprint` field already tells the AI client which vendor. Splitting tokens would force every consumer to write if/else.

---

*End of mini-PRD v0.1 for mk-qa-master v0.7.1. Cross-reference: `docs/prd-v0.7-visual-challenge.md`.*
