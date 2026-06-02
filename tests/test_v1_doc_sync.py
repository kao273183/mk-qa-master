"""v1.0.0 — Documentation sync tests (PR-2 of 4 stability-lock PRs).

Two invariants this file enforces:

1. **Tool-count sync** — every public-facing doc that claims a tool
   count (README, SKILL.md, reference/*.md) MUST match the count
   returned by `list_tools()`. v0.10 postmortem §9 #2 — the 19 → 21
   drift between v0.9.0 and v0.9.5 escaped CI for months because the
   docs and the server weren't tested together.

2. **Tool-name fidelity** — the docs in `skills/mk-qa-master/reference/`
   that enumerate tool names must include every tool that's actually
   registered. Easy to forget when adding a tool (the v0.10 PR-1 to
   PR-4 each added `plan_id` to one tool but never checked the
   reference docs listed them).

Decision history
----------------
- v1.0 PRD §11 #3 — scope is README + SKILL.md + reference/*.md
  (3 public-doc surfaces). Walkthroughs / blog drafts excluded —
  those are timestamped artifacts, not contracts.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
DOC_SURFACES = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "skills" / "mk-qa-master" / "SKILL.md",
]
REFERENCE_DIR = REPO_ROOT / "skills" / "mk-qa-master" / "reference"


def _current_tool_count() -> int:
    """How many tools the live server actually exposes via list_tools().
    This is the single source of truth — every doc claim must match."""
    from mk_qa_master.server import list_tools

    async def _gather():
        return await list_tools()

    return len(asyncio.run(_gather()))


def _docs_to_check() -> list[Path]:
    """README + SKILL.md + every .md in skills/mk-qa-master/reference/."""
    docs = list(DOC_SURFACES)
    if REFERENCE_DIR.is_dir():
        docs.extend(sorted(REFERENCE_DIR.glob("*.md")))
    return docs


# ---- Tool count sync (postmortem §9 #2) ----------------------------------

# Pattern that catches the doc phrasings actually used in this repo:
#   "21 tools", "21 MCP tools", "21-tool surface"
#
# The negative lookbehind `(?<![.\d])` excludes digits preceded by a
# dot or another digit, so version refs like "v0.7 tools" or "v0.10
# tools" don't get mistaken for tool counts. The trailing "tool(s)"
# word constrains it further.
_TOOL_COUNT_PATTERN = re.compile(
    r"(?<![.\d])(\d+)[\s-](?:MCP\s)?(?:core\s)?tools?\b",
    re.IGNORECASE,
)


def test_doc_tool_counts_match_live_server():
    """Every doc that names a tool count must match `list_tools()`.

    Catches the postmortem §9 #2 failure mode: a doc still says
    '19 MCP tools' months after v0.9.0 grew the surface to 21.
    """
    expected = _current_tool_count()
    mismatches: list[str] = []

    for path in _docs_to_check():
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for line_num, line in enumerate(text.splitlines(), start=1):
            for match in _TOOL_COUNT_PATTERN.finditer(line):
                claimed = int(match.group(1))
                # The pattern catches numbers in non-tool-count contexts
                # too (e.g., "5 core tools" referring to the bookend
                # tools). Skip when the leading word is clearly the
                # bookend subset and not the full surface.
                excerpt_lower = line.lower()
                if "bookend" in excerpt_lower or "5 core tools" in excerpt_lower:
                    continue
                if claimed != expected:
                    rel = path.relative_to(REPO_ROOT)
                    mismatches.append(
                        f"  {rel}:{line_num} claims {claimed} tools "
                        f"(live server has {expected}): {line.strip()!r}"
                    )

    if mismatches:
        pytest.fail(
            f"Doc tool-count drift detected — live server exposes "
            f"{expected} tools but docs say otherwise:\n"
            + "\n".join(mismatches)
            + "\n\nFix: either update the doc to match, or change the "
            "server's tool list (and rerun the v1.0 snapshot test so "
            "BREAKING_CHANGE_ACK is set explicitly)."
        )


# ---- Tool-name fidelity --------------------------------------------------

def _live_tool_names() -> set[str]:
    from mk_qa_master.server import list_tools

    async def _gather():
        return await list_tools()

    return {t.name for t in asyncio.run(_gather())}


def test_tool_surface_doc_lists_every_live_tool():
    """The reference cheatsheet at
    `skills/mk-qa-master/reference/tool-surface.md` exists specifically
    to document every tool. If a new tool ships but this doc doesn't
    name it, the LLM-side discovery story breaks.

    Soft check: tool name must appear as a substring somewhere in the
    file. Doesn't enforce ordering or formatting — the doc structure
    is allowed to evolve, just not silently drop a tool.
    """
    doc = REFERENCE_DIR / "tool-surface.md"
    if not doc.is_file():
        pytest.skip("reference/tool-surface.md not present; PR-3 owns it")

    text = doc.read_text(encoding="utf-8")
    missing = sorted(name for name in _live_tool_names() if name not in text)
    assert not missing, (
        f"reference/tool-surface.md doesn't mention these live tools: "
        f"{missing}. Add a row per missing tool, or remove them from "
        f"server.py if they're truly gone."
    )
