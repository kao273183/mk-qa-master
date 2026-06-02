# mk-qa-master v0.10.0 — Universal Bookend (mini-PRD)

**Status:** Draft v0.1 · **Author:** Jack Kao (kao273183) · **Date filed:** 2026-06-02 · **Successor to:** v0.9.x series (qa_plan / verify_plan introduced v0.9.1, integrated into `run_api_security_scan` v0.9.4) · **Theme picked:** v0.10 planning §3 Theme A

Mini-PRD (12 sections, same shape as `docs/prd-v0.8-api-security.md`). The strategic decision to pursue this theme is captured in `docs/v0.10-planning.md` §3. This document is the **execution contract** — what gets shipped, in what shape, in what order.

---

## 1. Vision

> **`qa_plan` is how you use mk-qa-master, not a special feature of one tool.**

After v0.9.4 the bookend pattern (`qa_plan → tool(plan_id=…) → response.plan_verification`) exists on exactly ONE tool (`run_api_security_scan`). v0.10 generalizes it to the four other meaningful tools — `run_tests`, `solve_visual_challenge`, `analyze_url`, `auto_generate_tests` — so that **the same workflow** (declare CPs upfront → run → auto-verify) applies across the whole product surface.

After v0.10, qa_plan covers **5 of 21 MCP tools (24%)**, up from 1 of 21 (5%). The rest are pure-data tools (history, summary, reports) where a CP-driven verification adds no value.

---

## 2. Problem Statement

The current state is incoherent. A host LLM that learned the v0.9.4 pattern (plan → scan → verification) sees the **same shape** of work in `run_tests`, `solve_visual_challenge`, etc., but **no `plan_id` arg** on those tools. It has two bad options:

1. Don't use qa_plan with those tools → loses the Webwright critical-points discipline that v0.9.x invested in.
2. Manually wire `verify_plan` after the tool call → boilerplate, error-prone, host-LLM-prompt-engineering burden.

This is the "bookend pattern works on one tool" smell. Either we generalize it (Theme A) or we admit it's an API-security-specific feature and remove the broader claims from SKILL.md. v0.10 picks the former.

---

## 3. MVP Scope (v0.10.0)

**In scope:**

- `plan_id: str | None = None` arg on **four new tools** (in this PR order):
  1. `run_tests` — evidence: pytest report.json rows (per-test result)
  2. `solve_visual_challenge` — evidence: single-record solve outcome (status + token presence)
  3. `analyze_url` — evidence: discovered modules (per-module record)
  4. `auto_generate_tests` — evidence: generated test files + modules they cover
- Per-tool **evidence shape contract** documented in this PRD §5
- `plan_verification` envelope appears in every above tool's response when `plan_id` is supplied — same shape as v0.9.4's `run_api_security_scan`
- Per-tool dogfood test: real artifact (real pytest run, real captcha fixture, real analyze_url against a known page, real auto_generate_tests run) → CPs ratified to expected values → verify resolves with the right pass/fail mix
- README + SKILL.md updates: "qa_plan works on 5 tools, here's the shape per tool"
- v0.10.0 release bumping pyproject + PyPI publish via the existing `publish.yml` workflow

**Explicitly out of scope:**
- Adding `plan_id` to read-only / pure-data tools (`get_test_history`, `get_report_summary`, `get_failure_details`, `verify_plan`, `qa_plan` itself, ...). These return existing artifacts; CP-verification adds nothing.
- Auto-creating plans from tool descriptions (host LLM is still the author of CPs — server doesn't infer)
- Changing the `_Plan` / CP / evidence schema itself — v0.9.x froze those for a reason
- A YAML-driven plan format — that's Theme C scope, separate PRD
- Adding more CP `kind` types beyond what v0.9.1 introduced (the existing set covers all four new evidence streams)

**v0.10.0 timeline:** **6–8 working days** of code (one day per PR plus a release-day PR). 4 PRs × small, mechanical changes + 1 release PR.

---

## 4. Architecture — No Changes

The v0.9.4 pattern in `run_api_security_scan` is the template:

```python
def run_api_security_scan(
    ...
    plan_id: str | None = None,
) -> dict:
    # ... main work ...
    result = {...}

    if plan_id:
        from ..tools.qa_plan import verify_plan_tool
        verify_result = verify_plan_tool({
            "plan_id": plan_id,
            "evidence": <evidence rows>,
        })
        result["plan_verification"] = verify_result

    return result
```

Every new tool integration follows this verbatim. The only knob is `<evidence rows>`. §5 below documents that knob for each tool.

No changes to `qa_plan`, `verify_plan`, the `_Plan` dataclass, the disk-persistence layer, or the LRU cache. v0.10 is **purely additive** at the tool-call surface.

---

## 5. Evidence Shape — Per Tool

The `evidence` arg to `verify_plan_tool` is `list[dict]`. The shape per tool defines which CPs can be authored against it.

### 5.1 `run_tests(plan_id=…)`

Evidence stream: **one row per test result**, transformed from pytest report.json's `tests` array.

```python
[
    {
        "nodeid": "tests/test_signup.py::test_happy_path",
        "outcome": "passed",        # passed | failed | skipped | xfailed | xpassed
        "duration": 0.234,           # seconds
        "longrepr": "..." or None,   # failure traceback (when outcome=failed)
        "kind": "test",              # discriminator for CP matchers
    },
    ...
]
```

Compatible CP kinds (from v0.9.1):
- `test_passed` — matches `outcome=passed` for a specific `nodeid`
- `test_failed` — matches `outcome=failed` for a specific `nodeid`
- `suite_duration_under` — sums `duration` across all rows
- `test_count_at_least` — counts rows where `outcome=passed`

If pytest report.json is missing (the runner failed before generating it), evidence is `[]` and verify_plan surfaces `evidence_empty` for every CP — same as v0.9.4 behavior.

### 5.2 `solve_visual_challenge(plan_id=…)`

Evidence stream: **single record** describing the solve outcome.

```python
[
    {
        "kind": "captcha_solve",
        "status": "passed",          # passed | failed | continue | expired | error
        "token_populated": True,     # bool — was a non-empty token returned
        "rounds_used": 0,            # int — multi-round dynamic-replace counter
        "fingerprint": "recaptcha-v2-image-4x4",
        "challenge_id": "abc...",
    }
]
```

The full token is **never** included in evidence — the token is a credential, telemetry hygiene from v0.7.0 forbids logging it. `token_populated: bool` is enough for CPs to assert "solve succeeded."

Compatible CP kinds:
- `captcha_solved` (new mapping — uses the generic `record_matches` kind from v0.9.1 against `status=passed AND token_populated=true`)

If the call returns `status: "continue"` (multi-round mid-loop), evidence is still emitted but `token_populated=false`. CPs asserting "solved" will return `failing` — the host LLM is expected to loop until `status=passed` before checking the plan.

### 5.3 `analyze_url(plan_id=…)`

Evidence stream: **one row per discovered module**.

```python
[
    {
        "kind": "ui_module",
        "module_type": "form",       # form | cta | nav | tab_bar | search | media | other
        "selector": ".signup-form",  # CSS selector, when extractable
        "url": "https://...",        # the URL being analyzed
        "confidence": 0.92,          # 0..1 — analyzer's confidence
    },
    ...
]
```

Compatible CP kinds:
- `module_discovered` (new mapping — matches `module_type=X` exists in evidence)
- `module_count_at_least` (matches `count >= N`)

### 5.4 `auto_generate_tests(plan_id=…)`

Evidence stream: **one row per generated test file**.

```python
[
    {
        "kind": "generated_test",
        "path": "tests/test_signup_form.py",
        "covers_module": "form",     # which analyze_url module this targets
        "test_count": 3,             # number of test functions in the file
    },
    ...
]
```

Compatible CP kinds:
- `file_generated` (new mapping — matches `path` exists)
- `module_covered` (matches `covers_module=X` exists)
- `total_tests_at_least` (sums `test_count` across rows)

---

## 6. Consent / Safety

No new consent gates. The tools that already gate (`solve_visual_challenge`, `run_api_security_scan`) keep their existing gates. `plan_id` is additive and never bypasses any consent check — when consent is missing, the tool returns its existing consent envelope **before** touching qa_plan.

Specifically: a `solve_visual_challenge(plan_id=X, _page=Y)` call without `QA_VISUAL_CHALLENGE_CONSENT=true` returns `consent_required` — no plan verification attempted, plan stays untouched. Same for `run_api_security_scan`.

This preserves the v0.7 / v0.8 ratchet: every safety check fires before any side-effect, including before the plan is loaded.

---

## 7. Tests

Per PR:

### PR-1 (`run_tests`)

```
test_run_tests_with_plan_id_emits_test_outcome_evidence
test_run_tests_with_plan_id_attaches_plan_verification
test_run_tests_without_plan_id_unchanged                  # regression — legacy callers
test_run_tests_with_unknown_plan_id_surfaces_plan_not_found
test_run_tests_with_no_report_json_yields_empty_evidence
```

### PR-2 (`solve_visual_challenge`)

```
test_solve_with_plan_id_emits_captcha_solve_record
test_solve_with_plan_id_excludes_token_from_evidence       # privacy invariant
test_solve_continue_status_marks_token_populated_false
test_solve_without_plan_id_unchanged
test_solve_with_consent_missing_skips_plan_load
```

### PR-3 (`analyze_url`)

```
test_analyze_url_with_plan_id_emits_one_row_per_module
test_analyze_url_module_count_at_least_cp_resolves
test_analyze_url_without_plan_id_unchanged
test_analyze_url_with_unknown_plan_id_surfaces_plan_not_found
```

### PR-4 (`auto_generate_tests`)

```
test_auto_generate_with_plan_id_emits_one_row_per_file
test_auto_generate_total_tests_at_least_cp_resolves
test_auto_generate_module_covered_cp_matches_analyze_url_findings
test_auto_generate_without_plan_id_unchanged
```

Dogfood verification (one per PR, manual + CI):
- PR-1: `examples/sample_api_project` — run_tests with a plan asserting "test_smoke passes, suite < 30s"
- PR-2: `examples/sample_captcha_fixture` — solve with a plan asserting "captcha_solved AND token_populated"
- PR-3: a deliberately-fixed-shape staging URL — plan asserts "form module discovered AND CTA module discovered"
- PR-4: chained dogfood — analyze_url → auto_generate → plan asserts "≥1 file per discovered module"

CI: each PR's new tests run on the existing `smoke · Python {3.10..3.13}` matrix. No new CI job (PRs are small enough).

---

## 8. Implementation Plan

| PR | Days | Scope | Closes |
|---|---|---|---|
| PR-1 | 1.5 | `run_tests(plan_id=…)` + evidence transform + 5 unit tests + dogfood | sub-task 1 of theme A |
| PR-2 | 1.5 | `solve_visual_challenge(plan_id=…)` + evidence + privacy hygiene test + 5 unit tests + dogfood | sub-task 2 |
| PR-3 | 1.5 | `analyze_url(plan_id=…)` + evidence + 4 unit tests + dogfood | sub-task 3 |
| PR-4 | 1.5 | `auto_generate_tests(plan_id=…)` + evidence + 4 unit tests + dogfood | sub-task 4 |
| PR-5 | 1 | README + SKILL.md narrative + version bump + tag + release | v0.10.0 ship |
| **Total** | **~7d** | | |

Each PR is **independently merge-able and PyPI-shippable**. Worst case, theme A ships as v0.10.0 (PR-1), v0.10.1 (PR-2), … rather than waiting for all four. This is the "every PR ships value alone" claim from the planning doc.

Subagent path: one delegation per PR is reasonable. Don't bundle multiple tools into one PR — the dogfood matters too much to compress.

---

## 9. Roadmap Context

This PRD lives at `docs/prd-v0.10-universal-bookend.md` to keep it discoverable next to its siblings (`prd-v0.7-*`, `prd-v0.8-*`). After v0.10.0 ships:

- `docs/v0.10-planning.md` §8 says "after whichever theme ships, this doc gets a postmortem section." That postmortem will reference back to this PRD.
- v0.11+ candidates from `docs/v0.10-planning.md` §2 (B, C, E) get re-evaluated. v0.10's outcome — especially how much surface-area work qa_plan actually saves once it's everywhere — informs whether B (v1.0 stability lock) is the next natural move.

Theme A explicitly **does not** include:
- Cloudflare Turnstile (planning §2 Theme D — high risk, parked)
- OWASP API4 rate limit (planning §2 Theme E — defer to v0.11+)
- YAML config (planning §2 Theme C — separate PRD if picked)

---

## 10. Decisions Required Before Coding

1. **PR-1 tool target** — `run_tests` (proposal) vs `run_failed` (a sibling that re-runs only failing tests)? Recommend: **`run_tests`** — it's the canonical entry point and `run_failed`'s evidence shape is a strict subset.
2. **Token-in-evidence policy for solve_visual_challenge** — confirmed `token_populated: bool` only, never the full token? Recommend: **yes, hard rule** — matches v0.7.0 telemetry-hygiene NFR.
3. **Evidence emission when consent missing** — emit `[]` (current `run_api_security_scan` behavior) or skip the plan-verification call entirely? Recommend: **skip entirely** — plan stays untouched, response surfaces only the consent envelope.
4. **Module-confidence threshold in `analyze_url` evidence** — include all modules regardless of confidence, or filter ≥ 0.5? Recommend: **all modules, with `confidence` in the row** — let CPs decide whether to filter via the `confidence >= X` matcher (already supported by v0.9.1 record_matches).
5. **Cumulative release cadence** — ship v0.10.0 after PR-5 (proposal) or release each PR as a patch (v0.10.{0..3} → v0.10.0 final)? Recommend: **one v0.10.0 after PR-5** — cleaner upgrade story for users; "qa_plan is now everywhere" is one announcement, not four.
6. **Backward-compat invariant test** — add an explicit test that "every plan-bookend-supporting tool also works with `plan_id=None`"? Recommend: **yes** — single parameterized test, asserts the legacy contract holds for the four new tools.

---

## 11. Decisions Ratified

Locked 2026-06-02:

1. **PR-1 target = `run_tests`** — canonical entry point; `run_failed` evidence is a strict subset, no need to design twice.
2. **Token never in evidence — `token_populated: bool` only** — hard rule. Honors v0.7.0 telemetry-hygiene NFR. Putting the raw token in evidence would route it into `verify_plan`'s disk-persistence path → credential leakage. CPs assert "solved" via the bool.
3. **Consent missing → skip plan-verification entirely** — plan stays untouched; response surfaces only the consent envelope. Prevents "no-consent" runs from polluting plan history. Matches the v0.7 / v0.8 invariant of "safety check before any side-effect."
4. **All modules in `analyze_url` evidence, including `confidence`** — no server-side filtering. CPs can filter via the existing v0.9.1 `record_matches` `confidence >= X` matcher. Don't decide for the user.
5. **One v0.10.0 release after PR-5** — cleaner upgrade story. "qa_plan covers 5 tools" is one announcement, not four patch releases.
6. **Add parametrized backward-compat test** — single test, asserts every plan-bookend tool returns its v0.9.5 shape when `plan_id` is omitted. Cheap (CI ≲ 0.1 s) and load-bearing for the "purely additive" claim.

---

## 12. Process Invariants Honored

From `docs/v0.10-planning.md` §5:

1. ✅ Mini-PRD before action — this document, before PR-1.
2. ✅ Dogfood against real artifacts — every PR has a dogfood step §7.
3. ✅ Version-sync invariant — PR-5 bumps pyproject + tags; the existing `test_plugin_versions_match_pyproject` enforces parity.
4. ✅ PyPI Summary 512-char limit — PR-5 description update will pass through the v0.9.5 regression test.
5. ✅ Spike validates produced VALUE — each PR's dogfood asserts the verification envelope renders as expected, not just `rc=0`.
6. ✅ Consent gates document themselves — §6 explicitly notes consent envelopes echo verbatim, no paraphrase.

---

*End of mini-PRD v0.1 for mk-qa-master v0.10.0. Cross-reference: `docs/v0.10-planning.md` (strategic context), `docs/prd-v0.8-api-security.md` (template + bookend reference implementation).*
