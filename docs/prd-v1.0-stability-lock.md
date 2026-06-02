# mk-qa-master v1.0.0 — Stability Lock (mini-PRD)

**Status:** Draft v0.1 · **Author:** Jack Kao (kao273183) · **Date filed:** 2026-06-02 · **Successor to:** v0.10.0 (Universal Bookend) · **Theme picked:** v0.11 planning §3 — B-then-G

Mini-PRD (12 sections, same shape as `docs/prd-v0.10-universal-bookend.md`). v0.11 planning §3 ratified the B-then-G ordering: this release is **v1.0.0**, not v0.11.0 — the bump to 1.0 is the entire point. v0.12+ is reserved for Theme G (Edge AI).

---

## 1. Vision

> **Promise the surface. Lock the shape. Make it possible to pin a version.**

Between v0.7.0 (Aug 2026 in this timeline — the AI Visual Challenge Solver line) and v0.10.0 (June 2026 — Universal Bookend), mk-qa-master added 4 release lines, doubled its tool count (12 → 21), and shipped two new tool genres (visual-challenge, API-security). Every step was additive, but the cumulative drift is real — a user pinning `mk-qa-master==0.9.4` and upgrading to `0.10.0` got a `plan_verification` field they didn't ask for on multiple tools.

v1.0.0 is the contract: the surface stops drifting until v2.0. New tools require a major bump. Breaking changes require explicit `BREAKING_CHANGE_ACK` + a deprecation period. A snapshot test enforces it in CI so this isn't aspirational — it's mechanical.

---

## 2. Problem Statement

Right now:
- A user reading `README.md` sees "21 MCP tools", but if they install last week's tag they get 19. There's no signal that the surface grew.
- A host LLM that learned the v0.9.5 schema doesn't know which fields are forever-stable vs. "this might move in v0.11".
- The Glama plugin catalog scores stability — pre-1.0 packages are penalized vs. 1.0+ packages of equivalent quality.
- Engineering consumers (the ones who pay) need a "we won't break this without warning" promise before they wire mk-qa-master into shared CI/CD.

The fix is mechanical, not philosophical: snapshot the schema, document the deprecation policy, write the migration guide, ship v1.0.0, mean it.

---

## 3. MVP Scope (v1.0.0)

**In scope:**

- **Snapshot tests in CI** that freeze the v1.0 contract:
  - Tool list (21 tools, by name + order)
  - Each tool's `inputSchema` (required fields, types, defaults)
  - Each tool's response shape on the happy path (top-level keys + their types)
  - `_Plan` / Critical Point / `Finding` / `plan_verification` shapes
  - Each evidence row shape per the v0.10 bookend (5 tools × their §5 evidence contract)
- **`BREAKING_CHANGE_ACK` env override** — when a deliberate breaking change ships, the PR sets this env in CI; absence makes the snapshot test fail.
- **`docs/MIGRATION-0.x-to-1.0.md`** — explicit list of v0.x → v1.0 changes (`plan_verification` semantics, `_coord_method` field, hCaptcha fingerprint chain, etc.) with before/after examples.
- **`docs/DEPRECATION-POLICY.md`** — formal: one minor-version of warning before any breaking change in v1.x. Removal at v2.0. `DeprecationWarning` raised through Python's `warnings` module, surfaced verbatim in MCP tool descriptions.
- **README v1.0 section** announcing the stability promise + linking to migration + deprecation docs.
- **Tool-count sync test** — single 10-line test (v0.10 postmortem §9 #2) asserting every doc's claimed tool count == count in `server.py`. Catches the 19 → 21 drift mode permanently.
- **`pyproject.toml` description audit** — current is 438/512 chars (v0.9.5 hotfix limit honored). v1.0 description doesn't need rewriting unless we change it for marketing; if we do, regression test catches over-512.
- **Version sync hardening** — v0.10 postmortem §9 #3: replace `startswith("0.11.")` style hardcoded assertions with a softer check (semver-parseable + ≥ pyproject floor).

**Explicitly out of scope:**

- **Adding any new tool.** v1.0's surface is exactly the 21 tools that exist as of v0.10.0. New runners, new MCP tools, new OWASP rules → all are v1.1+ work. Theme G (Edge AI) is explicitly v1.x territory per v0.11 planning §3.
- **Renaming tools** — even cosmetic improvements like `run_failed` → `rerun_failed_tests` are breaking. Hold for v2.0 or a deliberate deprecation cycle.
- **Removing env vars.** v0.11 planning §2 C (YAML config) is additive-only when it lands; v1.0 keeps all 13 env vars.
- **Performance optimizations.** v1.0 is about API stability, not internals.
- **The PR description templates** (v0.10 postmortem §9 #4 nice-to-have). Defer to v1.1 housekeeping.

**v1.0.0 timeline:** **~5 working days** of code + writing + a release-day PR. 4 PRs.

---

## 4. Architecture — No Code Changes

v1.0 is pure stability mechanics. No production-code change beyond:

1. Adding the schema-snapshot test infrastructure (new test files)
2. Adding the tool-count sync test
3. Updating the version-sync test softness

The 21 tools, the bookend pattern, the runners, the consent gates, the fingerprint table — **all unchanged**. The release notes specifically say "no behavior change; this release exists to promise stability."

---

## 5. Snapshot Test Mechanics

```python
# tests/test_v1_schema_snapshot.py
"""v1.0.0 schema snapshot. CI fails on any diff without
BREAKING_CHANGE_ACK=true. To intentionally evolve the schema in v1.x:
  1. Update tests/snapshots/v1_schema.json
  2. Set BREAKING_CHANGE_ACK=true in the PR's CI workflow
  3. Add a row to docs/MIGRATION-0.x-to-1.0.md (or a v1.x changelog)
"""

import json
import os
from pathlib import Path

import pytest

from mk_qa_master.server import _build_tools  # the tool factory


SNAPSHOT = Path("tests/snapshots/v1_schema.json")


def _current_schema():
    return [
        {
            "name": t.name,
            "inputSchema": t.inputSchema,
        }
        for t in _build_tools()
    ]


def test_tool_surface_matches_v1_snapshot():
    expected = json.loads(SNAPSHOT.read_text())
    current = _current_schema()
    if expected == current:
        return
    if os.environ.get("BREAKING_CHANGE_ACK", "").lower() == "true":
        # Intentional break — write the new snapshot and let CI pass.
        SNAPSHOT.write_text(json.dumps(current, indent=2))
        return
    pytest.fail(
        "Tool surface drift detected without BREAKING_CHANGE_ACK=true. "
        "Either revert the API change, OR if intentional set the env var "
        "and update docs/MIGRATION-0.x-to-1.0.md."
    )
```

Equivalent tests cover: `_Plan` dataclass shape, evidence rows per tool, `Finding` dict shape from API security. One snapshot file per shape class for git-diff clarity.

---

## 6. Deprecation Policy

`docs/DEPRECATION-POLICY.md` codifies:

1. **No silent removals.** Any tool / arg / response field flagged for removal raises `DeprecationWarning` for ≥ one minor version before going away.
2. **Warning channel.** The warning text appears in: (a) Python `warnings`, (b) the MCP tool description (so host LLMs see it), (c) the next minor release notes.
3. **Removal in major bumps only.** Tool removal happens at v2.0, never within v1.x.
4. **Schema additions are not breaking.** A new optional `inputSchema` field, a new response key, a new tool — all minor bumps in v1.x. They get the BREAKING_CHANGE_ACK=true treatment only if they constrain existing input (e.g., a new required field on an existing tool).
5. **Snapshot evolution.** When BREAKING_CHANGE_ACK is set, the PR MUST update `docs/MIGRATION-0.x-to-1.0.md` (or its v1.x successor `MIGRATION-1.x.md`). CI verifies this with a `git diff --quiet docs/MIGRATION-*.md`-style check inside the same PR.

---

## 7. Migration Guide Contents

`docs/MIGRATION-0.x-to-1.0.md` enumerates every shape change between v0.7.0 (the start of the "modern" surface) and v1.0.0:

- v0.7.3 → v0.7.4: `solve_visual_challenge` gained `status: "continue"` (additive)
- v0.7.4 → v0.10.0: `solve_visual_challenge` gained `rounds_used`, `token_populated`, MCP `ImageContent` (additive)
- v0.8.0 → v0.9.4: `run_api_security_scan` gained `plan_verification` when `plan_id` supplied (additive)
- v0.9.5 → v0.10.0: `run_tests` / `analyze_url` / `auto_generate_tests` gained `plan_verification` likewise (additive)
- ...

Format per entry: `Before` snippet, `After` snippet, `Action required` (usually "none — additive").

Plus a "what stays stable forever in v1.0" section listing the 21 tool names + every consent gate env var + the `_Plan` shape, with the explicit promise that touching any of them requires a v2.0.

---

## 8. Implementation Plan

| PR | Days | Scope | Closes |
|---|---|---|---|
| PR-1 | 1.5 | Snapshot tests + BREAKING_CHANGE_ACK plumbing + `tests/snapshots/v1_schema.json` seed | sub-task 1 |
| PR-2 | 1.5 | Tool-count sync test (v0.10 postmortem §9 #2) + soft version-pin test (#3) | sub-task 2 |
| PR-3 | 1 | `MIGRATION-0.x-to-1.0.md` + `DEPRECATION-POLICY.md` | sub-task 3 |
| PR-4 | 1 | README v1.0 section + version bump + tag + release | v1.0.0 ship |
| **Total** | **~5d** | | |

Each PR is independently merge-able. Worst case PR-3 / PR-4 land as v0.11.0 first then a v1.0.0 rename if scope creeps; but `B-then-G` design is to ship v1.0.0 in one shot.

---

## 9. Roadmap Context

This PRD lives at `docs/prd-v1.0-stability-lock.md` so it's discoverable next to its siblings (`prd-v0.7-*`, `prd-v0.8-*`, `prd-v0.10-*`). After v1.0.0 ships:

- `docs/v0.11-planning.md` §8 gets a postmortem section.
- v1.1+ planning opens with Theme G (Edge AI) primed per v0.11 §3.
- The schema-snapshot tests become load-bearing for every future v1.x PR.

v1.0 explicitly **does not** include:
- Edge AI runner (Theme G — reserved for v1.1+)
- OWASP API4 rate limit (Theme E — v1.1+ candidate)
- YAML config UX (Theme C — v1.1+ candidate)
- Cloudflare Turnstile (Theme D — pruned)

---

## 10. Decisions Required Before Coding

1. **Snapshot location** — `tests/snapshots/v1_schema.json` (proposal — one file) vs `tests/snapshots/v1/*.json` (per-shape file)? Recommend: **per-shape directory** — better git-diff clarity when only one shape changes.
2. **`BREAKING_CHANGE_ACK` enforcement** — fail-on-set (manual override) vs fail-on-set-AND-no-migration-doc-update (require docs edit in same PR)? Recommend: **fail-on-no-migration-update** — without the migration entry, the override is a bypass.
3. **Tool-count sync test scope** — README + SKILL.md only, or also walkthroughs / blog drafts in repo? Recommend: **README + SKILL.md + reference/*.md** (3 surfaces) — the public docs only. Walkthroughs are timestamped artifacts.
4. **Version bump cadence in v1.x** — patch-only-for-bugfix-strict (1.0.1 = bugfix; 1.1.0 = feature) or current loose pattern? Recommend: **strict semver**, codified in DEPRECATION-POLICY.md.
5. **`pyproject.toml` description rewrite for v1.0** — keep current 438-char description, or rewrite to emphasize the stability promise? Recommend: **rewrite** to lead with "Stable since v1.0.0" — that's the marketing differentiation. Watch the 512-char limit.
6. **Deprecation policy enforcement** — automated `DeprecationWarning` emission test, or honor-system? Recommend: **automated** — add a fixture that captures warnings during the snapshot tests and asserts any new deprecation flag is paired with a `warnings.warn(...)` call.

---

## 11. Decisions Ratified

Locked 2026-06-02:

1. **Per-shape snapshot files under `tests/snapshots/v1/`** — better git-diff clarity. One file per shape class (`tool_surface.json`, `plan_shape.json`, `finding_shape.json`, `evidence_row_<tool>.json`).
2. **`BREAKING_CHANGE_ACK=true` + migration-doc edit required** — CI fails when the ack is set but `docs/MIGRATION-*.md` wasn't touched in the same commit. Prevents the ack from becoming a silent bypass.
3. **Tool-count sync covers `README.md` + `skills/mk-qa-master/SKILL.md` + `skills/mk-qa-master/reference/*.md`** — the 3 public-doc surfaces. Walkthroughs / drafts excluded (timestamped artifacts).
4. **Strict semver in v1.x** — codified in `DEPRECATION-POLICY.md`. Patch = bugfix only, minor = additive feature, major = breaking. CI's version-bump test enforces format; reviewer enforces semantic correctness.
5. **`pyproject.toml` description rewritten for v1.0** to lead with "Stable since v1.0.0". Watch the 512-char limit; regression test from v0.9.5 already enforces.
6. **Automated `DeprecationWarning` enforcement** — a test fixture captures warnings during snapshot tests; any tool description containing the substring "deprecated" must have a matching `warnings.warn(..., DeprecationWarning)` call somewhere in its code path.

---

## 12. Process Invariants Honored

From `docs/v0.11-planning.md` §5 (which extends v0.10's invariants):

1. ✅ Mini-PRD before action — this document, before PR-1.
2. ✅ Dogfood against real artifacts — PR-1's snapshot tests dogfood themselves: the first run *creates* the snapshot, subsequent runs assert it.
3. ✅ Version-sync invariant — PR-2 hardens this exact mechanism with a soft semver check.
4. ✅ PyPI Summary 512-char limit — §10 #5 explicitly calls this out for v1.0's description rewrite.
5. ✅ Spike validates produced VALUE — PR-1's snapshot test fails on intentional drift unless `BREAKING_CHANGE_ACK=true`, proving the test catches real changes.
6. ✅ Consent gates document themselves — unchanged from v0.7.
7. ✅ Tool-count refs sync test — explicitly added in PR-2 (v0.10 postmortem §9 #2).
8. ✅ Soft version-pin tests — explicitly added in PR-2 (v0.10 postmortem §9 #3).
9. ⏸️ PR description templates — deferred to v1.1 housekeeping per §3 out-of-scope.

---

*End of mini-PRD v0.1 for mk-qa-master v1.0.0. Cross-references: `docs/v0.11-planning.md` (strategic context, B-then-G recommendation), `docs/prd-v0.10-universal-bookend.md` (latest PRD template + surface state at v1.0 freeze).*
