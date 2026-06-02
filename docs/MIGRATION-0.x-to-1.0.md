# Migration Guide — mk-qa-master 0.x → 1.0

This document enumerates every shape change in the MCP tool surface between v0.7.0 (the start of the "modern" surface) and v1.0.0. **Every change is additive** — v0.x → v1.0 should be a drop-in upgrade for callers that ignore newly-added fields.

The promise of v1.0 is that any further change to the shape requires either (a) a minor-version bump for additive changes, or (b) a major-version bump (v2.0+) for breaking removals. See [`DEPRECATION-POLICY.md`](DEPRECATION-POLICY.md) for the formal policy.

---

## TL;DR for upgraders

If you're on v0.10.0, upgrading to v1.0.0 is **a no-op**. The schema is identical. v1.0 exists to *promise* that schema, not to change it.

If you're on v0.9.5 or earlier, read the entries below for the bookend feature additions; otherwise pin and move on.

---

## Change log — additive only

### v0.7.0 → v0.7.4 (AI Visual Challenge Solver, multi-round)

**Solve response gained `status: "continue"` (dynamic-replace mode).**

Before (v0.7.0–0.7.3):
```jsonc
{ "status": "passed" | "failed" | "expired", "token": "..." | null, ... }
```

After (v0.7.4+):
```jsonc
{
  "status": "passed" | "failed" | "expired" | "continue",
  "token": "..." | null,
  "rounds_used": 0,                  // NEW — present on continue
  "screenshot_base64": "...",        // NEW — present on continue (also surfaced as MCP ImageContent)
  ...
}
```

Action required: clients that switch on `status` should add a `continue` branch that calls `solve_visual_challenge` again with the next tile selection. Pass `selected_tile_indices: []` to finalize.

---

### v0.7.3 → v0.7.4 (MCP ImageContent for screenshots)

**`inspect_visual_challenge` + `solve_visual_challenge` (continue mode) now return the screenshot as native MCP `ImageContent` in addition to the embedded `screenshot_base64` field.**

Multimodal AI clients see the image directly instead of decoding base64. No code change for clients that consume the JSON; clients that previously parsed `screenshot_base64` still get it.

---

### v0.8.0 (OWASP API Security)

**New tool `run_api_security_scan`** + new MCP tool `run_api_security_scan` for OWASP API Top 10 scanning. Net surface +1.

No breaking change to anything else.

---

### v0.9.1 (qa_plan + verify_plan prelude)

**Two new MCP tools** `qa_plan` and `verify_plan`. Webwright critical-points pattern: declare success up front, verify against evidence after.

Net surface +2. No change to any existing tool.

---

### v0.9.4 (Bookend on run_api_security_scan)

**`run_api_security_scan` gained optional `plan_id` arg.** When supplied, the response gains a `plan_verification` envelope.

Before:
```jsonc
{ "findings": [...], "summary": {...} }
```

After (when `plan_id` supplied):
```jsonc
{
  "findings": [...],
  "summary": {...},
  "plan_verification": {            // NEW
    "plan_id": "...",
    "status": "passed" | "incomplete" | "failed",
    "checklist": [...],
    ...
  }
}
```

Omitting `plan_id` keeps the v0.8 response shape verbatim.

---

### v0.10.0 (Universal Bookend across 5 core tools)

**Four more tools gained the `plan_id` arg + `plan_verification` response envelope:**

- `run_tests(plan_id=…)` — evidence is pytest-json-report's `tests` rows
- `solve_visual_challenge(plan_id=…)` — evidence is a single record `{kind: "captcha_solve", status, token_populated: bool, rounds_used, fingerprint, challenge_id}`. **Raw token never appears in evidence** (privacy invariant).
- `analyze_url(plan_id=…)` — evidence is one row per discovered module
- `auto_generate_tests(plan_id=…)` — evidence is one row per generated test file (success or failure)

Same additive pattern as v0.9.4. Omitting `plan_id` keeps the legacy shape.

See [`prd-v0.10-universal-bookend.md`](prd-v0.10-universal-bookend.md) §5 for the full per-tool evidence shape contract.

---

## What stays stable forever in v1.0

The following won't change without a v2.0 major bump (per [`DEPRECATION-POLICY.md`](DEPRECATION-POLICY.md)):

### Tool surface — exactly 21 tools

```
get_runner_info, list_tests, run_tests, run_failed, get_test_report,
get_failure_details, generate_test, codegen, generate_html_report,
get_test_history, get_optimization_plan, analyze_url, analyze_screen,
init_qa_knowledge, get_qa_context, inspect_visual_challenge,
solve_visual_challenge, auto_generate_tests, run_api_security_scan,
qa_plan, verify_plan
```

### Consent gate env vars

```
QA_VISUAL_CHALLENGE_CONSENT
QA_VISUAL_CHALLENGE_AUTHORIZED_DOMAINS
QA_VISUAL_CHALLENGE_TIMEOUT
QA_API_SECURITY_CONSENT
QA_API_SECURITY_AUTHORIZED_DOMAINS
```

### Plan / bookend shapes

`_Plan`, critical-point dict shape, `plan_verification` envelope shape, evidence-row shape per bookend tool. Locked in v1.0.

### Hard-stop blacklists

The forbidden-domain lists (third-party identity providers refused regardless of consent) for `solve_visual_challenge` and `run_api_security_scan` remain enforced. New domains can be added (additive) but existing entries don't get removed in v1.x.

---

## How to deliberately evolve the schema in v1.x

When an intentional change is needed:

1. Set `BREAKING_CHANGE_ACK=true` in the PR's CI env. The v1.0 snapshot test (`tests/test_v1_schema_snapshot.py`) rewrites itself instead of failing.
2. Add a new entry to this file with before/after JSON snippets.
3. If the change is additive (new optional field, new tool), bump minor (v1.x → v1.x+1).
4. If the change is breaking (renamed field, removed tool, type change), the PR needs:
   - A `DeprecationWarning` in v1.x (one minor of warning before removal)
   - The actual removal in the next major (v2.0)

The snapshot test's rewrite path is the explicit acknowledgment that something is changing. The migration entry is the audit trail.

---

*Last updated: 2026-06-02 (v1.0 stability lock PR-3). Cross-reference: [`DEPRECATION-POLICY.md`](DEPRECATION-POLICY.md), [`prd-v1.0-stability-lock.md`](prd-v1.0-stability-lock.md).*
