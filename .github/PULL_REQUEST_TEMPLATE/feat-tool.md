<!--
Template for: new MCP tool addition.

Use this when the PR adds a new Tool() entry in list_tools() and a
new dispatch handler. Triggers BREAKING_CHANGE_ACK=true on the
schema-snapshot test.

For new runners (no MCP tool added), use feat-runner.md.
For qa_plan bookend extensions on existing tools, use feat-bookend.md.
For a release PR, use release.md.
-->

## What changes

<!-- 1-2 sentences naming the new tool + its purpose. -->

| File | Purpose |
|---|---|
| `src/mk_qa_master/server.py` | New `Tool()` entry + dispatch handler |
| `src/mk_qa_master/tools/<name>.py` | Implementation |
| Tests | Unit coverage |

## Tool surface contract

```jsonc
// Input
{
  "<arg1>": "...",          // required
  "<arg2>": "..."           // optional
}

// Success response
{
  "<key1>": ...,
  "<key2>": ...
}

// Error envelopes
{ "error": "<error_kind>", "hint": "..." }
```

## v1.0 stability lock paperwork

- [ ] `tests/snapshots/v1/tool_surface.json` updated (`BREAKING_CHANGE_ACK=true`) — tool count goes N → N+1
- [ ] `docs/MIGRATION-1.x.md` entry added (REQUIRED — CI ack-check fails the PR otherwise)
- [ ] `docs/DEPRECATION-POLICY.md` consulted — confirm the addition fits the patch/minor/major matrix
- [ ] README + SKILL.md + `skills/mk-qa-master/reference/tool-surface.md` tool-count refs swept
- [ ] PyPI Summary length unchanged or re-trimmed if description was rewritten

## Test plan

- [ ] N new unit tests (list them)
- [ ] If consent-gated: tests assert consent_required envelope before any side effect
- [ ] 389+/389+ tests pass locally
- [ ] CI green (ack-check + snapshot + doc-sync all green)

## Next

<!-- What ships next -->
