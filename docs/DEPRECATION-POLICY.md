# Deprecation Policy — mk-qa-master v1.0+

This is the contract for how mk-qa-master's MCP tool surface evolves after v1.0.0. It exists so users who pin `mk-qa-master==1.x.y` can reason about what might break, when, and with what warning.

---

## TL;DR

| Bump | What's allowed |
|---|---|
| **Patch** (v1.0.0 → v1.0.1) | Bug fixes. No surface changes. No new tools, no new args, no schema additions. |
| **Minor** (v1.0.x → v1.1.0) | Additive changes only. New tools, new optional args, new response fields. Deprecation *announcements* allowed but no removals. |
| **Major** (v1.x.y → v2.0.0) | Removals + breaking changes allowed, only for things previously deprecated for ≥ 1 minor version. |

If you pin `mk-qa-master==1.x` your CI keeps working. If you pin `mk-qa-master==1.0.*` only bugfixes ever apply.

---

## What counts as breaking

| Change | Breaking? |
|---|---|
| Add a new tool | No (additive) |
| Add a new optional arg to an existing tool | No (additive) |
| Add a new field to a response | No (additive) |
| Add a new enum value to an existing `enum` field | **Yes** (callers may match on enum exhaustively) |
| Rename a tool | Yes |
| Rename an arg | Yes |
| Rename a response field | Yes |
| Remove a tool | Yes |
| Remove an arg | Yes |
| Remove a response field | Yes |
| Change a field's type | Yes |
| Make an optional arg required | Yes |
| Tighten an arg's validation (narrower range, stricter regex) | Yes |
| Change the meaning of an existing value (e.g., what `status: "passed"` implies) | Yes |
| Loosen an arg's validation | No (still accepts everything it did before) |

---

## The deprecation cycle

When a breaking change is desired:

1. **Announce in v1.x (≥ 1 minor version before removal).** The deprecated item must:
   - Emit a `DeprecationWarning` via `warnings.warn(...)` when used
   - Have "Deprecated:" in the MCP tool description so host LLMs see it
   - Have a migration entry in `docs/MIGRATION-1.x-to-2.0.md` (created when v2.0 work starts)

2. **Hold for ≥ 1 minor cycle.** If we announce in v1.3, the earliest removal is v2.0 — and v1.4 / v1.5 still ship with the deprecated item working.

3. **Remove at the next major.** v2.0 is the only place removals land. Patch (v1.x.y) and minor (v1.x) versions never remove anything.

This means an item announced as deprecated in v1.3 is **guaranteed working** in every v1.x release. Users who pin `==1.x` are safe; users who pin `>=1.3,<2` get the warning but no breakage.

---

## How the snapshot test enforces this

`tests/test_v1_schema_snapshot.py` freezes the MCP surface in `tests/snapshots/v1/tool_surface.json`. Any drift fails CI **unless** `BREAKING_CHANGE_ACK=true` is set.

Setting the ack is the explicit "I know this is a breaking change, here's why" signal. When set, the PR MUST also:

1. Add an entry to this file (or `MIGRATION-1.x-to-2.0.md` when the cycle reaches that point) explaining the change
2. Update the snapshot file (the test rewrites it automatically)
3. Make sure the deprecation cycle above is honored (if removing rather than adding)

The ack alone is not enough — without the documentation, the next v1.x release notes have no explanation for the schema change. Reviewers MUST gate-check this.

---

## Patch releases

Patch versions (v1.0.x) are reserved for bug fixes. Specifically:

- Behavior fixes — when the existing schema is right but the runtime is wrong (race condition, off-by-one, wrong env var lookup precedence, etc.)
- Performance fixes — no behavior change
- Doc fixes — typo, broken link, README update

No new tools. No new args. No new response fields. No `BREAKING_CHANGE_ACK=true` in a patch — if you need it, that's a minor at minimum.

---

## What "feature" means in minor versions

Minor bumps (v1.0 → v1.1) can ship:

- A new MCP tool (e.g., Theme G's `analyze_stream` from v0.11 planning)
- A new optional arg on an existing tool
- A new response field
- A new env var (consent gate or config)
- A new optional dependency (via `extras_require`)
- A new runner

What they can't ship:

- Renames (those need the deprecation cycle)
- Removals
- Required-arg additions (a v0.x caller would break)
- Stricter enum or validation (a v0.x caller's value might now error)

---

## Semver mapping

| Semver | mk-qa-master example |
|---|---|
| MAJOR | v1.0.0 → v2.0.0 — removal of deprecated items, OR license change (see "License changes" below) |
| MINOR | v1.0.0 → v1.1.0 — new tool / new optional arg |
| PATCH | v1.0.0 → v1.0.1 — bug fix |

This is the canonical semver mapping, no surprises. The CI test `test_pyproject_version_is_semver_and_at_or_above_floor` (added in v1.0 PR-2) enforces that the version string is parseable; reviewer enforces semantic correctness against the table above.

---

## License changes

License is part of the contract. A change in the project's license — even an "upgrade" toward more permissive terms — is treated as a **major version bump (v2.0+)** with a documented announcement cycle.

### Rules

1. **License changes only land at major version boundaries.** Never in a patch, never in a minor.
2. **Announcement required ≥ 1 minor version before the major bump.** The announcement lives in:
   - A dedicated `docs/RELICENSING.md` (or successor) document explaining the rationale, the timeline, and the user impact
   - A "License Evolution Plan" section in `README.md` with the same content in summary
   - An entry in the current `docs/MIGRATION-*.md`
3. **Historical releases stay under their original license forever.** A relicense affects only future versions tagged after the change. Past releases retain their original LICENSE perpetually — anyone with a v1.x release has perpetual rights under v1.x's license.
4. **v1.x line gets bugfix-only maintenance for ≥ 6 months after the major bump.** If the relicense happens at v2.0.0, v1.x.y patch releases continue under v1.x's original license for at least 6 months. After 6 months, v1.x enters reduced-support mode (security fixes only).
5. **Contributor consent**: before a relicense PR is merged, every unique contributor in `git log --all --pretty=format:%an` must have either signed off on the change OR have their commits limited to code subsequently rewritten by the relicensor. Solo-author projects skip this check trivially.

### Why even "upgrade" license changes get a major bump

A license is a contract. Even if the new license grants strictly more rights than the old one, downstream users who have legal review requirements need an unambiguous signal that the contract changed. Bundling the change into a major bump:

- Lets `docs/MIGRATION-*.md` cleanly split per major version
- Gives `pip install <package>==X.Y.*` semantics a meaningful "I want the old license" pin
- Honors the v1.0 stability lock spirit, not just the letter
- Bundles with other v2.x cleanups that were waiting for a major bump

The one extra version-number digit is worth the unambiguity.

### Example (announced 2026-06-03)

mk-qa-master v1.2.1 announced relicensing from MIT → Apache 2.0 in v2.0.0:

- v1.2.1: announcement (no LICENSE file change; `docs/RELICENSING.md` added)
- v1.3.0 → v1.x.y: hold cycle, still MIT
- v2.0.0: actual relicense, Apache 2.0 LICENSE file + NOTICE + source headers + manifest sync
- v1.x.y bugfix line: maintained under MIT for ≥ 6 months after v2.0.0 ships

See `docs/RELICENSING.md` for the full plan + mechanical checklist.

---

## Practical guidance for contributors

- Adding a new tool? **Minor bump.** Update README + SKILL.md tool count. The doc-sync test (added v1.0 PR-2) catches drift.
- Adding a new arg to an existing tool? **Minor bump.** Make it optional with a sensible default. The snapshot test catches the change — set `BREAKING_CHANGE_ACK=true` to acknowledge and re-snapshot.
- Renaming something? **Don't.** If you must, do the full deprecation cycle: ship the new name + alias the old name in v1.x with a `DeprecationWarning`, hold for at least one minor, then plan removal for v2.0.
- Adding an enum value? **Breaking** in theory; ack required. In practice, consumers should treat unknown enums gracefully — but the contract says ack.
- Fixing a bug? **Patch.** No surface change. If your fix changes the schema, it's not a patch, it's a minor with an ack.

---

*Effective: v1.0.0. Cross-reference: [`MIGRATION-0.x-to-1.0.md`](MIGRATION-0.x-to-1.0.md), [`prd-v1.0-stability-lock.md`](prd-v1.0-stability-lock.md).*
