# mk-qa-master v0.8.0 — Mobile WebView CAPTCHA Solver (mini-PRD)

> ⚠️ **PARKED — read [`docs/v0.8-mobile-postmortem.md`](v0.8-mobile-postmortem.md) before acting on this document.**
> The mega-YAML architecture below was built on a false assumption about Maestro's `runScript:` directive (it runs in a GraalJS sandbox, not in the device WebView). The Maestro-driver PRs (#54, #55) have been reverted. The Driver Protocol (#52–53) and sample app (#56) are kept. This PRD is preserved as a historical artifact — do not implement against it.

**Status:** ~~Draft v0.3 — post-spike, mega-YAML architecture locked~~ · **PARKED 2026-05-26** · **Author:** Jack Kao (kao273183) · **Last updated:** 2026-05-25

This is a **mini-PRD**, not the full 21-section format. v0.8.0 extends v0.7.x's AI Visual Challenge Solver (reCAPTCHA + hCaptcha + multi-round dynamic-replace) from desktop browser to mobile WebView. Same fingerprint table, same consent model, same multi-round loop — different driver layer.

Refer to [`docs/prd-v0.7-visual-challenge.md`](prd-v0.7-visual-challenge.md) for the desktop architecture this builds on.

---

## 1. Vision

> **The same AI Visual Challenge Solver, now inside the WebView of any iOS / Android app.**

When a QA engineer runs a Maestro flow against a staging build and hits a reCAPTCHA / hCaptcha inside an embedded WebView (OAuth screen, signup form, payment confirmation), v0.7 leaves them stuck — desktop Playwright can't reach into a mobile WebView. v0.8 adds a Maestro-driven path: same `inspect_visual_challenge` + `solve_visual_challenge` tools, but the underlying driver layer screenshots the device, evals JS in the WebView, and taps at coordinates instead of clicking with mouse.

**No new MCP tools.** Mobile detection is a `_driver` hint on the existing tools. Tool count stays at 18.

---

## 2. Problem Statement

User-side: mobile QA via Maestro is the only cross-platform mobile-UI test path in mk-qa-master (v0.6.0 runner). The moment a staging build's signup flow surfaces a captcha challenge inside its embedded WebView, the engineer either:
- Manually solves it on the device every test run (≈ 30 s of human time per CI run), or
- Disables captcha in staging via a backend flag (often forbidden by ops, or requires backend coordination), or
- Skips the captcha-protected paths entirely (test coverage gap).

Architecturally: a captcha inside a WebView is still HTML + JS. The fingerprint table from v0.7.3 (selectors for reCAPTCHA / hCaptcha) **applies unchanged**. What differs is the driver:
- Desktop: Playwright `page.screenshot()`, `frame_locator.locator()`, `page.mouse.click()`
- Mobile: Maestro CLI — every primitive operation requires generating a temporary YAML flow file and invoking `maestro test`. There are no direct `maestro tap-at-coord` / `maestro eval-js` CLI commands.

Adding mobile is replacing a driver, not designing a new solver — but the driver layer has non-trivial subprocess + temp-file overhead that v0.7 didn't have to deal with.

---

## 3. MVP Scope (v0.8.0)

**In scope:**
- New `_driver` field on `inspect_visual_challenge` / `solve_visual_challenge`: `"playwright"` (default, v0.7 behavior) | `"maestro"` (new)
- Maestro driver class that wraps the CLI by **generating ad-hoc YAML flows** for each primitive (screenshot / runScript / tapOn). Temp-file lifecycle handled internally.
- New `_maestro_device_id` field for multi-device targeting (replaces the earlier `_maestro_session_id` proposal — Maestro is stateless subprocess, "session" was the wrong abstraction)
- iOS Simulator + Android Emulator support (real devices via Maestro's existing auto-discovery)
- Same fingerprint table (reCAPTCHA, hCaptcha) — no vendor changes
- Multi-round dynamic-replace flow (inherited from v0.7.4)
- Same consent gate (`QA_VISUAL_CHALLENGE_CONSENT`) + domain allowlist + hard-stop blacklist
- Token extraction via `runScript output.token` with a documented size limit + file-fallback path for tokens > 1024 chars
- 7-8 new unit tests covering Maestro driver code paths (subprocess + temp-file mocking) — bumped from §3 draft v0.1's 4-5 because the YAML-generation + subprocess-failure code paths need their own coverage
- New CI job `visual-challenge-maestro-ios` on `macos-latest` + iOS Simulator + Maestro CLI

**Explicitly out of scope:**
- Native (non-WebView) iOS / Android captcha UIs — vendors rarely ship these; user can manual-solve
- Cloudflare Turnstile mobile — pure behavior scoring, no challenge to inspect (same exclusion as v0.7)
- Appium / XCTest / Espresso runners — adds heavy deps; defer to v0.9 if demand surfaces
- WebView token extraction via native bridge (`WKScriptMessageHandler` etc.) — fall back to runScript output capture + file path for oversized tokens
- React Native / Capacitor / Cordova-specific shims — these all use the same WebView under the hood
- Per-op Maestro test invocations (one `maestro test` per tap / screenshot / runScript) — confirmed infeasible by spike (§4 has the numbers). MVP **must** use the mega-YAML architecture below.
- Android Emulator on `macos-latest` CI — incompatible (KVM unavailable on Apple Silicon runners). Android validation goes through manual local dogfood + a ubuntu-latest CI job if proven needed in v0.8.1.

**v0.8.0 timeline:** **~7-9 working days** of code, plus tests + CI + docs + PR + release. Confirmed post-spike (§10 #7 ratified). ≈ 5× v0.7.4's footprint because of the new driver layer + mega-YAML generator + mobile fixture app + cross-platform validation.

---

## 4. Architecture Differences from v0.7.x

### Spike findings (2026-05-25)

Measurements that drove the mega-YAML pivot:

| Operation | Wall-clock | Notes |
|---|---|---|
| `maestro hierarchy` (cold, includes one-time iOS driver install) | 30 s | first call only |
| `maestro test` with `launchApp` + `screenshot` (no tap) | 28.6 s | baseline overhead |
| `maestro test` with `launchApp` + 1 tap + screenshot | 33.5 s | adds ~5 s for 1 tap |
| `maestro test` with 5 taps + screenshot in one YAML | 36.5 s | **+0.8 s for 4 extra taps** (~200 ms each) |
| `maestro test` with 10 taps + screenshot | 53.5 s | +17.8 s for 9 extra taps (~2 s each) |
| `maestro test` with 20 taps + screenshot | 64.0 s | linear from N>5, ~1.5 s each |
| `runScript: \|<inline JS>` | FAIL — Maestro 2.6.0 reads the `\|` as a file path |

**Root cause**: every `maestro test` pays a fixed ~28 s for subprocess + JVM startup + iOS driver attach + app verify + cleanup. The actual op (tap / screenshot) is sub-second. Below 5 taps in one flow, Maestro is even cheaper than that — close to free. Above 5, an internal `waitForAnimationEnd` retry loop kicks in.

The original v0.7-style "one MCP tool call → one Maestro subprocess" design is therefore infeasible: 3 dynamic-replace rounds × 5 tile clicks each × 30 s = ~9 minutes per CAPTCHA solve. Below.

### Driver layer abstraction — mega-YAML mode

Same `VisualChallengeDriver` protocol as the desktop side, but `MaestroDriver` operates in **"build the whole solve as one YAML, execute once"** mode rather than per-op:

```python
class VisualChallengeDriver(Protocol):
    def screenshot_iframe(self) -> bytes: ...
    def eval_js_in_iframe(self, script: str) -> Any: ...
    def tap_at(self, x: int, y: int) -> None: ...
    def get_iframe_bbox(self) -> dict: ...
    def get_cell_bboxes(self, count: int) -> list[dict]: ...

    # New for v0.8: batch-mode primitives used by MaestroDriver to collapse
    # an entire inspect/solve cycle into one mega-YAML run.
    def begin_batch(self) -> None: ...
    def commit_batch(self) -> dict: ...   # runs the assembled YAML, returns all results
```

Two implementations:
- `PlaywrightDriver` — wraps the existing v0.7.x code paths (refactor, not rewrite). `begin_batch / commit_batch` no-op — Playwright is fast enough per-op that batching adds no value.
- `MaestroDriver` — new; **accumulates ops in a list, generates one big YAML on `commit_batch`, runs one `maestro test`**.

### Mega-YAML shape

For a typical inspect → AI judge → solve cycle, the YAML produced looks like:

```yaml
appId: ${app_id}
---
# Phase 1: inspect — capture screenshot + DOM probe results in one shot.
- launchApp:
    clearState: false
- takeScreenshot: ${session_id}__inspect
- runScript: ./tmp/${session_id}_probe.js   # writes cell bboxes + DPR into output.*

# Phase 2: solve — AI client has already returned tile_indices.
# Mega-YAML inlines all clicks + verify + token-read in ONE flow.
# Each tile click is generated by the driver from the AI selection.
- tapOn: { point: "${cell_0_x_pct}%, ${cell_0_y_pct}%" }
- tapOn: { point: "${cell_2_x_pct}%, ${cell_2_y_pct}%" }
- tapOn: { point: "${cell_5_x_pct}%, ${cell_5_y_pct}%" }
- tapOn: { id: "recaptcha-verify-button" }
- waitForAnimationToEnd:
    timeout: 5000
- runScript: ./tmp/${session_id}_token.js   # writes the response-token to output.token
- takeScreenshot: ${session_id}__verified
```

The MCP-level flow looks the same to AI clients (call inspect → think → call solve), but **inspect now caches the screenshot + cell bboxes + DPR client-side** until `solve` arrives, and `solve` triggers the single mega-YAML run.

### Trade-off the AI client gives up

In desktop (v0.7), the AI client could see a screenshot, then mid-flow call `inspect_visual_challenge` again to look at the updated state. In mobile v0.8 mega-YAML, **the whole solve runs as one Maestro subprocess** — the AI client returns its tile selection once, then waits ~37 s for the full cycle to complete. No mid-flow intervention.

For multi-round dynamic-replace flows (v0.7.4): each *round* is a separate mega-YAML invocation. So 3 rounds = 3 × ~37 s ≈ 2 minutes total, same shape as v0.7.4 multi-round desktop, just with batched Maestro inside each round.

### Maestro CLI reality check

The available top-level commands and their callsites in v0.8:

| Capability | Top-level CLI? | How v0.8 uses it |
|---|---|---|
| Full-device screenshot | `maestro screenshot` ✅ direct | not used — folded into mega-YAML |
| Hierarchy dump (find WebView) | `maestro hierarchy` ✅ direct | one-time on driver init (~30 s warm-up) |
| Tap at coords | ❌ no direct CLI | YAML `tapOn: { point: "x,y" }` inside the mega-YAML |
| Eval JS in WebView | ❌ no direct CLI | YAML `runScript: ./file.js` (inline `\|` broken in 2.6.0) — driver writes temp JS files |

### Coordinate translation

Mobile devices have a **device pixel ratio** (DPR) — iPhone 15 is 393×852 logical but 1179×2556 physical. Screenshots are full-resolution; tap coordinates use logical pixels.

`MaestroDriver.tap_at(x, y)` converts:
- If x, y come from a screenshot's pixel coords → divide by DPR to get logical for tapOn
- DPR comes from the probe JS (run once per session, written into `output.dpr` of the mega-YAML)
- **Also read `window.innerWidth` and the screenshot width** to validate the conversion — iOS WebView viewport meta scaling can produce non-integer effective DPR
- Convert logical px → percentage of viewport for tap (`tapOn: { point: "X%, Y%" }`) — DPR-independent

Cache DPR per device_id — only changes if user rotates / swaps device.

### Token extraction strategy

Default path: a probe JS file written into `tmp/` runs `output.token = document.querySelector(selector).value`. Maestro CLI exposes this in the test output. The driver reads from the maestro test stdout.

**Size limit caveat**: Maestro's `runScript` output values have historically had a ~1024 char cap. reCAPTCHA tokens are ~400 chars (under the cap, fine). hCaptcha tokens can run 800-1500 chars (often over).

Fallback when token > 1024 chars: the probe JS writes the full token to `localStorage` under a known key, the mega-YAML pulls it via a follow-up `runScript ./read_localstorage.js`. If that path also fails, fall back to file-based exfiltration via the app's Documents directory (`xcrun simctl io <UDID> pull` post-flow).

### Same fingerprint table

`_FINGERPRINTS` from v0.7.3 applies unchanged. The selectors target HTML elements that exist regardless of whether the page renders in a desktop browser or a mobile WebView. The `_coord_method: per_cell_bbox` path also works — same JS `getBoundingClientRect()` works in WebView.

### Maestro CLI reality check

The available top-level commands and their callsites in v0.8:

| Capability | Top-level CLI? | How v0.8 uses it |
|---|---|---|
| Full-device screenshot | `maestro screenshot output.png` ✅ direct | direct, no YAML needed |
| Hierarchy dump (find WebView) | `maestro hierarchy` ✅ direct | direct, also used by v0.6 analyzer |
| Tap at coords | ❌ no direct CLI | generate temp YAML with `tapOn: { point: "x,y" }` + `maestro test temp.yaml` |
| Eval JS in WebView | ❌ no direct CLI | generate temp YAML with `runScript`, capture output |

So screenshot + hierarchy are cheap; **tap + eval cost a full Maestro subprocess startup each**. Spike (§10 #7) measures the real wall-clock.

### Coordinate translation

Mobile devices have a **device pixel ratio** (DPR) — iPhone 15 is 393×852 logical but 1179×2556 physical. Screenshots are full-resolution; tap coordinates use logical pixels.

`MaestroDriver.tap_at(x, y)` converts:
- If x, y come from a screenshot's pixel coords → divide by DPR to get logical for tapOn
- DPR comes from `runScript "() => window.devicePixelRatio"` on first call per device
- **Also read `window.innerWidth` and the screenshot width** to validate the conversion — iOS WebView viewport meta scaling can produce non-integer effective DPR

Cache DPR per device_id — only changes if user rotates / swaps device.

### Token extraction strategy

Default path: `runScript` returns the textarea value via `output.token = document.querySelector(selector).value`. Maestro CLI exposes this in the test output.

**Size limit caveat**: historically Maestro's `runScript` return value has had a ~1024 char cap. reCAPTCHA tokens are ~400 chars (under the cap, fine). hCaptcha tokens can run 800-1500 chars (often over).

Fallback when token > 1024 chars: the runScript writes the full token to a known file path (e.g., `/data/local/tmp/qa_captcha_token.txt` on Android, the app's Documents directory on iOS) and `runScript` returns only a SHA256 fingerprint + file path. The driver then `adb pull` / `xcrun simctl io push` (or Maestro's own file pull) to retrieve the full token.

If this fallback proves unreliable in practice, deferred for v0.8.1.

### Same fingerprint table

`_FINGERPRINTS` from v0.7.3 applies unchanged. The selectors target HTML elements that exist regardless of whether the page renders in a desktop browser or a mobile WebView. The `_coord_method: per_cell_bbox` path also works — same JS `getBoundingClientRect()` works in WebView.

---

## 5. Tool Surface

**Zero new MCP tools.** Existing surface gains two optional arguments (both default to behavior-preserving values):

```jsonc
{
  "tool": "inspect_visual_challenge",
  "args": {
    "_driver": "maestro",              // NEW — defaults to "playwright"
    "_maestro_device_id": "iPhone-15"  // NEW — optional, pins target device
  }
}
```

`_maestro_device_id` is matched against Maestro's `--device` flag value. When omitted, Maestro's auto-discovery picks the first connected device. When `_driver="maestro"` is set but Maestro CLI isn't on PATH, surface:

```json
{
  "error": "no_maestro_cli",
  "retryable": false,
  "hint": "_driver=maestro requires Maestro CLI on PATH. Install: brew install maestro"
}
```

When `_driver="maestro"` is set but no device is reachable:

```json
{
  "error": "no_active_maestro_device",
  "retryable": false,
  "hint": "..."
}
```

AI clients consuming desktop captcha solving need **no code change** — `_driver` defaults to `"playwright"` and v0.7 behavior is preserved.

---

## 6. Consent / Safety

v0.7 §13 NFR carries verbatim:

- `QA_VISUAL_CHALLENGE_CONSENT=true` required (default false)
- `QA_VISUAL_CHALLENGE_AUTHORIZED_DOMAINS` enforced when set
- Same hard-stop blacklist (third-party identity providers refused regardless of consent) — `accounts.google.com`, `appleid.apple.com`, `login.microsoftonline.com`, Discord, etc. all still hard-stopped on mobile WebView too
- Telemetry: boolean outcome only, no screenshots / challenge text / tile selection
- New env: `QA_VISUAL_CHALLENGE_MOBILE_DEVICE_ID` — optional, pins which device Maestro targets if multiple connected. Default: Maestro's auto-discovery (first connected).

**Mobile-specific addition**: detect if the page domain is reachable via the WebView's user agent — if WebView is configured to bypass captcha for staging IPs, surface a warning suggesting Tier 1 bypass instead of using the AI solver.

**No hard-stop additions for mobile**: the existing v0.7 OAuth provider list already covers the providers most apps embed in WebView.

---

## 7. Tests + 3-tier Dogfood Pyramid

### Unit tests

Add to `tests/test_visual_challenge.py`:

```
test_inspect_uses_maestro_driver_when_requested
test_inspect_returns_error_when_maestro_cli_missing
test_inspect_returns_error_when_no_device_reachable
test_maestro_driver_taps_at_logical_coords            # DPR conversion
test_maestro_driver_evals_js_in_webview
test_maestro_driver_assembles_mega_yaml_correctly     # NEW — verify YAML shape
test_maestro_driver_handles_yaml_generation_failure
test_maestro_driver_subprocess_timeout                # cap at QA_TIMEOUT_SECONDS
test_token_extraction_oversized_falls_back_to_localstorage
test_dpr_cache_keyed_by_device_id
```

Subprocess calls mocked via `unittest.mock.patch("subprocess.run", ...)` — same pattern as existing Maestro runner tests. Real-device validation lives in the dogfood pyramid below.

### Dogfood pyramid

Three layers of progressively more realistic validation. Mirrors the v0.7.4 desktop dogfood structure (`dogfood-real-recaptcha.yml`), extended to mobile.

| Tier | Where | What it validates | When it runs | Cost |
|---|---|---|---|---|
| **1 — Mock fixture** (`examples/sample_captcha_mobile_app/`) | iOS Simulator on `macos-latest` CI | Plumbing: Maestro driver assembles YAML correctly, mega-YAML executes, screenshot + tap + token round-trip works against an in-app WKWebView loading `examples/sample_captcha_fixture/index.html`. Fixture verify always passes — this exercises the *pipeline*, not click accuracy. | Every PR touching `visual_challenge.py` or `runners/maestro.py` | Free (~3 min/run) |
| **2 — Google reCAPTCHA test keys** (same sample app, `withTestKey.html`) | iOS Simulator on `macos-latest` CI | Real reCAPTCHA JS embedded in WebView using Google's [public test sitekey](https://developers.google.com/recaptcha/docs/faq) (`6LeIxAcTAAAAAJcZVRqyHh71UMIEGNQ_MXjiZKhI`). Validates "we can interact with real Google reCAPTCHA JS in a mobile WebView" without burning real Google quotas. Click selection still doesn't matter (test key passes anything). | Nightly via `dogfood-mobile-test-keys.yml` | Free (~3 min/run) |
| **3 — Real reCAPTCHA / hCaptcha demo page** | iOS Simulator on `macos-latest` CI + the canonical vendor demo pages | Real Google traffic + real challenge images + real AI judgment (via `closed_loop_solver.py --provider claude`). Measures actual success rate + latency against production-grade challenges. **Canonical URLs (same as v0.7.4 desktop dogfood):** `https://www.google.com/recaptcha/api2/demo` (reCAPTCHA v2), `https://accounts.hcaptcha.com/demo` (hCaptcha). Both are vendor-maintained dev sandboxes — safe to dogfood against, no ToS concern. Optional override via env `QA_MOBILE_DOGFOOD_URL` for users with their own staging build. | Nightly via `dogfood-mobile-real-recaptcha.yml` + manual `workflow_dispatch` | macos-latest minutes + Claude API tokens (~$0.05/run) |

### Sample mobile app for Tier 1/2

`examples/sample_captcha_mobile_app/` — minimal Xcode + Gradle projects, each:

- A single `WKWebView` (iOS) / `WebView` (Android) full-screen
- Reads URL from launch arg / Intent (default: bundled `index.html` for Tier 1, `withTestKey.html` for Tier 2)
- A "Submit" button that checks if the captcha response textarea is non-empty
- Logs success to NSLog / logcat so the Maestro flow can `assertVisible: "captcha-ok"`

Ship pre-built `.app` / `.apk` artifacts in the repo so the CI doesn't have to build them. Add `examples/sample_captcha_mobile_app/README.md` covering: how to rebuild the artifact, what selectors the v0.8 driver looks for, how to swap the loaded URL for local dev.

### CI workflows

Add three workflows mirroring v0.7.4's `dogfood-real-recaptcha.yml`:

- `.github/workflows/visual-challenge-maestro-ios.yml` (Tier 1, PR-triggered)
- `.github/workflows/dogfood-mobile-test-keys.yml` (Tier 2, nightly)
- `.github/workflows/dogfood-mobile-real-recaptcha.yml` (Tier 3, nightly + manual)

All three use `macos-latest` + `xcrun simctl` to boot an iPhone simulator + install bundled `.app` + `maestro test` the assertions.

### Android coverage

Tier 1 Android job lives on `ubuntu-latest` runners (Android x86 emulator works on Linux/KVM, not macOS Apple Silicon). Fires only when the PR touches Android-specific code. Tier 2/3 Android is deferred to v0.8.1 unless usage demands it.

---

## 8. Implementation Plan

Post-spike, mega-YAML is the locked architecture (§4). Steps below reflect real complexity.

| Step | Time | What |
|---|---|---|
| ~~0~~ | ~~done~~ | ~~Spike: `scripts/spike-maestro-perf.py` — output ratified in §10 #7~~ |
| 1 | 1d | `VisualChallengeDriver` protocol + refactor existing v0.7 paths to `PlaywrightDriver` (no behavior change for desktop) |
| 2 | 2d | `MaestroDriver` mega-YAML mode — accumulate ops in `begin_batch`, generate single YAML on `commit_batch`, temp JS file management, subprocess wrapping with QA_TIMEOUT_SECONDS, DPR cache keyed by device_id |
| 3 | 0.5d | Wire `_driver` + `_maestro_device_id` args through `inspect_visual_challenge_tool` + `solve_visual_challenge_tool`; cache inspect result client-side until matching solve arrives |
| 4 | 1.5d | Sample mobile fixture iOS app (`examples/sample_captcha_mobile_app/`) — Xcode project + bundled `.app` artifact + iframe-content HTML variants for Tier 1 / Tier 2 |
| 5 | 0.5d | Tier 1 CI workflow `visual-challenge-maestro-ios.yml` — PR-triggered, ~3 min/run |
| 6 | 0.5d | Tier 2 CI workflow `dogfood-mobile-test-keys.yml` — nightly |
| 7 | 0.5d | Tier 3 CI workflow `dogfood-mobile-real-recaptcha.yml` — nightly + workflow_dispatch, uses Claude API |
| 8 | 1.5d | Unit tests (10 cases) + manual real-device dogfood iOS Simulator + Android Emulator on local Linux box |
| 9 | 0.5d | README + walkthrough updates (mobile captcha section, mega-YAML caveats), `examples/sample_captcha_mobile_app/README.md` |
| 10 | 0.5d | v0.7 PRD §24 ratification append + bump `pyproject.toml` to 0.8.0 + PR |
| **Total** | **~9d** | Plus PR / CI / release time. Tightens to ~7d if Android coverage is fully deferred (skip step 8's Android leg). |

Subagent can take steps 1-7 in one delegation. Step 8+ is local work.

---

## 9. Roadmap Context

This PRD lives at `docs/prd-v0.8-mobile-webview-captcha.md` to keep v0.7's big PRD focused. After ratification, append `## 24. v0.8 ratified` to the v0.7 PRD with a cross-reference.

Subsequent work:
- v0.8.1 — Maestro driver batched-flow optimization (combine N ops into one Maestro test), Android CI, token-extraction fallback hardening — only if real usage surfaces the need
- v0.8.2 — Cloudflare Turnstile mobile (only if a real customer asks; behavior-scoring captchas are out of scope by design)
- v0.9.0 — Pact + `analyze_api` (returns to the API testing arc, completes v0.6 line)
- v1.0.0 — bundled documentation site + full PRD lockdown

---

## 10. Decisions Required Before Coding

1. **Driver argument naming** — `_driver: "playwright" | "maestro"` (current) vs `_platform: "desktop" | "mobile"`?
   > **Ratified: `_driver`.** More accurate (Maestro can drive a real iOS device, "mobile" is ambiguous).

2. **Maestro session identification** — pass `_maestro_session_id` per-call vs `_maestro_device_id`?
   > **Ratified: `_maestro_device_id`.** Maestro is a stateless subprocess — every `maestro test` re-attaches to the device. "Session" was the wrong abstraction.

3. **CI strategy** — Maestro Cloud free tier vs `macos-latest` + iOS Simulator?
   > **Ratified: `macos-latest` + iOS Simulator.** Free, reproducible, no dependency on Maestro Cloud's free-tier quota.

4. **iOS-first vs Android-first vs both for v0.8.0** —
   > **Ratified: BOTH driver code (Maestro CLI is platform-agnostic), but CI runs iOS only.** Android Emulator on `macos-latest` is incompatible with Apple Silicon (no KVM); pushing Android to a ubuntu-latest CI job or v0.8.1 keeps MVP small.

5. **DPR caching scope** — per-session (cached) vs per-call (re-read)?
   > **Ratified: per-device, re-read on `_maestro_device_id` change.** Orientation change is user-initiated and rare.

6. **Hard-stop blacklist additions for mobile** — Mobile-only auth providers?
   > **Ratified: none specific.** `accounts.google.com`, `appleid.apple.com`, etc. are already in the v0.7 list and they're the OAuth providers most apps embed in WebView.

7. **TIMELINE — RATIFIED post-spike** *(updated in draft v0.3)*
   > **Spike outcome** (run 2026-05-25, see `scripts/spike-maestro-perf.py` + the numbers in §4):
   >
   > - Per-`maestro test` fixed overhead is **~30 s** (JVM + iOS driver attach + app verify + cleanup) — wildly higher than the v0.2 estimate of 200-500 ms
   > - Within a single mega-YAML, **1-5 taps add ~200 ms each**; 6+ taps see Maestro's internal `waitForAnimationEnd` throttle and cost ~1.5-2 s each
   > - `runScript:` with inline JS (`|` block scalar) is broken in Maestro 2.6.0 — interpreted as a file path. Workaround: write JS to a temp file, reference it from the YAML
   >
   > **Decision: ship the mega-YAML architecture (option B from earlier discussion).** Pay one ~30 s `maestro test` invocation per inspect-then-solve round, batching all clicks + verify + token-read inside it. Multi-round dynamic-replace runs ~2 minutes total (3 rounds × ~37 s/round) versus the ~9 minutes a naive per-op design would cost.
   >
   > **Trade-off accepted**: the AI client returns a single tile selection per round and waits for the whole round to commit. No mid-flow intervention. v0.7.4's multi-round loop is preserved across mega-YAML invocations (each round = one Maestro subprocess), so the AI still gets a fresh inspect screenshot between rounds.
   >
   > Timeline: **7-9 days** depending on Android scope (see §8). Tightens to ~7 d if Tier 1 Android is fully deferred to v0.8.1.

---

## 11. Decisions Ratified

*(All 7 locked post-spike.)*

1. Driver argument: `_driver: "playwright" | "maestro"`
2. Session identification: `_maestro_device_id` (NOT `_maestro_session_id`)
3. CI strategy:
   - **Tier 1** (`visual-challenge-maestro-ios.yml`) — iOS on `macos-latest`, PR-triggered, ~3 min/run, free
   - **Tier 2** (`dogfood-mobile-test-keys.yml`) — iOS on `macos-latest`, nightly, free (real reCAPTCHA test sitekey)
   - **Tier 3** (`dogfood-mobile-real-recaptcha.yml`) — iOS on `macos-latest`, nightly + manual dispatch, uses Claude API (~$0.05/run)
   - Android Tier 1 on `ubuntu-latest` (Android x86 emulator); Android Tier 2/3 deferred to v0.8.1
4. Platform support: iOS + Android driver code, iOS CI in all 3 tiers; Android CI in Tier 1 only
5. DPR caching: per-device, re-read on `_maestro_device_id` change
6. Hard-stop additions: none mobile-specific
7. **Architecture: mega-YAML mode is MVP** — pay ~30 s Maestro fixed overhead once per round (multi-round inspect/solve cycles), assemble whole solve into one YAML and execute once. Trade-off: AI returns a single tile selection per round and waits ~37 s for that round's mega-YAML to commit; no mid-flow intervention within a round. Multi-round dynamic-replace preserved (each round = its own mega-YAML). Timeline 7-9 days (see §8).

---

*End of mini-PRD v0.2 for mk-qa-master v0.8.0. Cross-reference: `docs/prd-v0.7-visual-challenge.md` (architecture), `docs/prd-v0.7.1-hcaptcha.md` (mini-PRD template followed here), `scripts/spike-maestro-perf.py` (gate).*
