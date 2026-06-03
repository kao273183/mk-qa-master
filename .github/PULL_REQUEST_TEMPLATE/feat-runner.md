<!--
Template for: new test-runner family (like v1.1's edge / rtsp).

Use this when the PR adds a new entry to REGISTRY in
src/mk_qa_master/runners/__init__.py and a new subclass of TestRunner.

For new MCP tools (not runners), use feat-tool.md.
For extensions to the qa_plan/verify_plan bookend, use feat-bookend.md.
For a release PR, use release.md.
-->

## What changes

<!-- 1-2 sentences naming the runner + its target framework / platform. -->

| File | Purpose |
|---|---|
| `src/mk_qa_master/runners/<name>.py` | TestRunner subclass |
| `src/mk_qa_master/runners/__init__.py` | REGISTRY entry + alias |
| `pyproject.toml` | Optional extras (if heavy deps) |
| Tests | Unit coverage |

## Optional extras

<!-- Heavy deps (~100MB+) MUST be optional. List them here. Skip if
the runner has no heavy deps. -->

```toml
[project.optional-dependencies]
<name> = ["dep>=X,<Y"]
```

## Env vars introduced

<!-- Every QA_<NAME>_* env var. Default value, optional/required, what
it does. The doc-sync test catches stale README env-var table entries. -->

| Env var | Default | Purpose |
|---|---|---|
| `QA_<NAME>_FOO` | `default` | What it does |

## Stability lock paperwork

- [ ] `tests/snapshots/v1/tool_surface.json` updated (`BREAKING_CHANGE_ACK=true`) — **ONLY if a new MCP tool was added; runners alone don't change the tool surface**
- [ ] `docs/MIGRATION-1.x.md` entry added
- [ ] README "Per-runner snippet" section gains a new entry
- [ ] README runner-prerequisites table gains a new row
- [ ] SKILL.md tool-count refs unchanged (no new tool) OR bumped 22 → 23 (new tool)

## Test plan

- [ ] N new unit tests (list them)
- [ ] Heavy deps mocked via `patch.dict("sys.modules", ...)` so tests run on base install
- [ ] Manual end-to-end against bundled fixture (if shipped)
- [ ] 389+/389+ tests pass locally
- [ ] CI green

## Next

<!-- What ships next (PR-2 in the same series, or v1.x+1, etc.) -->
