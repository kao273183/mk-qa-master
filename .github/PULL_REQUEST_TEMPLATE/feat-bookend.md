<!--
Template for: adding qa_plan / plan_id bookend support to an existing
MCP tool (like v0.10's 4-PR series).

Use this when the PR adds a `plan_id: str | None = None` arg to an
existing tool + emits `plan_verification` in the response when supplied.

For brand-new tools, use feat-tool.md.
For brand-new runners, use feat-runner.md.
For a release PR, use release.md.
-->

## What changes

<!-- 1-2 sentences naming the tool being extended + the evidence shape. -->

`<tool_name>` gains optional `plan_id`. When supplied, the response gains a `plan_verification` envelope with evidence shape:

```jsonc
[
  {
    "kind": "<kind>",
    // ... per docs/prd-v0.10-universal-bookend.md §5
  }
]
```

## Decisions honored

| PRD § | Decision |
|---|---|
| #1 / #2 / #3 / #4 | (cite the v0.10 PRD ratifications you're following) |

## Privacy invariant

<!-- Required for tools handling credentials (tokens, API keys, session
cookies). Confirm what's NEVER in evidence: -->

- [ ] Raw token / credential NEVER appears in `plan_verification` (only `*_populated: bool` or equivalent)
- [ ] Dedicated test serializes the entire envelope and asserts the credential string is absent

## Early-return semantics

When does `plan_verification` attach?

| Status | plan_verification? |
|---|---|
| Pre-execute errors (consent / confirm / not_found) | ❌ |
| Post-execute outcomes (passed / failed / etc.) | ✅ |

## Test plan

- [ ] Backward compat: `plan_id=None` / omitted → shape unchanged vs prior version
- [ ] Happy path: evidence shape matches PRD §5 contract
- [ ] Error envelope surfacing: `plan_not_found` lands under `plan_verification`
- [ ] Privacy invariant test (if credential-handling tool)
- [ ] 389+/389+ tests pass locally

## v1.0 stability lock paperwork

Bookend additions add a `plan_id` arg → tool `inputSchema` changes → snapshot ack triggered.

- [ ] `tests/snapshots/v1/tool_surface.json` updated (`BREAKING_CHANGE_ACK=true`)
- [ ] `docs/MIGRATION-1.x.md` entry added
- [ ] CI ack-check green

## Next

<!-- Next tool in the bookend series, or v1.x+1 -->
