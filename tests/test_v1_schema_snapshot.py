"""v1.0.0 — Schema snapshot test (PR-1 of 4 stability-lock PRs).

This is the load-bearing test for the v1.0 stability promise. It
snapshots the MCP tool surface (every tool's name + inputSchema) into
`tests/snapshots/v1/tool_surface.json`. Subsequent runs diff against
that snapshot; any drift fails CI.

Intentional schema evolution
----------------------------
To deliberately evolve the surface during v1.x (e.g., add a new tool,
add an optional arg, deprecate something):

  1. Set `BREAKING_CHANGE_ACK=true` in the PR's CI env
  2. Update `docs/MIGRATION-0.x-to-1.0.md` (or its v1.x successor) with
     a before/after entry in the same commit
  3. The test then rewrites the snapshot file as part of its run and
     passes — the new file is the new contract

Without the ack, any change here fails CI. That's the entire point of
the v1.0 lock: forcing the intentional act.

Decision history
----------------
- v1.0 PRD §11 #1 — per-shape snapshot files under `tests/snapshots/v1/`
  (not one big file). Only `tool_surface.json` is enforced in PR-1;
  follow-up PRs in v1.x add `plan_shape.json`, `finding_shape.json`,
  and evidence-row snapshots per bookend tool.
- v1.0 PRD §11 #2 — `BREAKING_CHANGE_ACK=true` is gated on a paired
  migration-doc edit. That enforcement lands in PR-3 (the migration
  doc itself doesn't exist yet); for now, the ack alone unlocks the
  rewrite path.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest


SNAPSHOT_DIR = Path(__file__).resolve().parent / "snapshots" / "v1"
TOOL_SURFACE_SNAPSHOT = SNAPSHOT_DIR / "tool_surface.json"


def _current_tool_surface() -> list[dict]:
    """Invoke the server's `list_tools()` async fn and reduce each Tool
    to the v1.0-frozen subset: name + inputSchema. Tool descriptions are
    intentionally NOT snapshotted — we let docs evolve freely. The
    LLM-facing contract is the *callable shape*, not the prose."""
    from mk_qa_master.server import list_tools

    async def _gather():
        return await list_tools()

    tools = asyncio.run(_gather())
    return [
        {"name": t.name, "inputSchema": t.inputSchema}
        for t in tools
    ]


def _normalize(payload: list[dict]) -> str:
    """Canonical JSON form for deterministic diffing. sort_keys + indent
    2 + trailing newline matches what we write to the snapshot file."""
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def test_v1_tool_surface_matches_snapshot():
    """The 21-tool surface locked at v1.0.0 must remain stable. Any
    diff fails unless `BREAKING_CHANGE_ACK=true` is set — in which case
    the snapshot file is rewritten and the test passes (the PR is
    promising to be aware of the change)."""
    current = _current_tool_surface()
    current_text = _normalize(current)

    if not TOOL_SURFACE_SNAPSHOT.exists():
        pytest.fail(
            f"Snapshot file missing: {TOOL_SURFACE_SNAPSHOT}. "
            "PR-1 of the v1.0 stability lock seeds this file."
        )

    snapshot_text = TOOL_SURFACE_SNAPSHOT.read_text(encoding="utf-8")

    if snapshot_text == current_text:
        return

    if os.environ.get("BREAKING_CHANGE_ACK", "").lower() == "true":
        # Intentional schema evolution. Rewrite the snapshot — the next
        # run becomes the new baseline. The PR is responsible for the
        # paired migration-doc update (enforced in PR-3).
        TOOL_SURFACE_SNAPSHOT.write_text(current_text, encoding="utf-8")
        return

    # Surface a concrete name-level diff in the failure so the
    # reviewer doesn't have to eyeball the JSON dumps. Field-level
    # diffs (the kind that actually matter once tool count is stable)
    # are easier to spot from the file diff in `git diff`.
    snapshot_data = json.loads(snapshot_text)
    snapshot_names = {t["name"] for t in snapshot_data}
    current_names = {t["name"] for t in current}
    added = sorted(current_names - snapshot_names)
    removed = sorted(snapshot_names - current_names)

    detail_lines = []
    if added:
        detail_lines.append(f"Tools ADDED since v1.0 snapshot: {added}")
    if removed:
        detail_lines.append(f"Tools REMOVED since v1.0 snapshot: {removed}")
    if not (added or removed):
        detail_lines.append(
            "Tool name set unchanged but inputSchema diffs — run "
            "`git diff tests/snapshots/v1/tool_surface.json` after "
            "re-running this test with BREAKING_CHANGE_ACK=true to see "
            "the exact field-level changes."
        )

    pytest.fail(
        "v1.0 tool surface drift detected without BREAKING_CHANGE_ACK=true.\n"
        + "\n".join(detail_lines)
        + "\n\nTo accept the drift intentionally:\n"
        "  1. Set BREAKING_CHANGE_ACK=true in your CI env (and re-run)\n"
        "  2. Update docs/MIGRATION-0.x-to-1.0.md (or v1.x successor) "
        "with a before/after entry in the same commit\n"
        "  3. The test will rewrite the snapshot file and pass"
    )


def test_v1_snapshot_has_expected_tool_count():
    """Independent of drift checking: the v1.0 snapshot freezes 21
    tools. This catches the case where the snapshot file got rewritten
    locally with a different tool list but nobody updated the count
    references in README / SKILL.md."""
    snapshot_data = json.loads(TOOL_SURFACE_SNAPSHOT.read_text(encoding="utf-8"))
    assert len(snapshot_data) == 21, (
        f"v1.0 promises 21 tools; snapshot has {len(snapshot_data)}. "
        "If you intentionally changed the surface, also update the "
        "tool-count references in README.md + skills/mk-qa-master/SKILL.md "
        "(PR-2 will add an automated sync test for this)."
    )


def test_v1_snapshot_includes_all_bookend_tools():
    """The v0.10 bookend pattern is one of v1.0's biggest contracts.
    The 5 bookend tools MUST stay in the surface — removing any of
    them silently would break every host LLM that learned the
    qa_plan → tool(plan_id) → plan_verification idiom."""
    snapshot_data = json.loads(TOOL_SURFACE_SNAPSHOT.read_text(encoding="utf-8"))
    names = {t["name"] for t in snapshot_data}

    bookend = {
        "run_tests",
        "solve_visual_challenge",
        "analyze_url",
        "auto_generate_tests",
        "run_api_security_scan",
    }
    missing = bookend - names
    assert not missing, (
        f"v1.0 promises plan-bookend on {bookend}; missing from "
        f"snapshot: {sorted(missing)}"
    )

    # And each bookend tool MUST keep the plan_id property in its
    # inputSchema — the v0.10 surface promise.
    by_name = {t["name"]: t["inputSchema"] for t in snapshot_data}
    for tool in sorted(bookend):
        props = by_name[tool].get("properties", {})
        assert "plan_id" in props, (
            f"Bookend tool {tool!r} lost its plan_id arg in the "
            f"v1.0 snapshot — that's a v0.10 contract violation."
        )
