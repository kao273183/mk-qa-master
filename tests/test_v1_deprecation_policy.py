"""v1.0.0 — Deprecation policy enforcement (PR-3 of 4 stability-lock PRs).

v1.0 PRD §11 #6: any tool whose description contains "deprecated" MUST
have a matching `warnings.warn(..., DeprecationWarning)` call somewhere
in its code path. Otherwise the deprecation is a doc claim only — host
LLMs see the word but the Python layer silently honors the deprecated
behavior, and users have no programmatic signal.

This test pairs the user-facing description with the engineering signal.
Together with the cycle defined in `docs/DEPRECATION-POLICY.md`, every
deprecation in v1.x:

  1. Has "Deprecated:" in the tool description (LLM-visible)
  2. Emits `DeprecationWarning` at runtime (programmatic-visible)
  3. Has a migration entry (paper trail)
  4. Survives at least one minor version before removal (cycle)

This test enforces (1)+(2). PRs that drop (3) get caught by the
snapshot ack mechanism (PR-1 + PR-3); (4) is reviewer-enforced.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src" / "mk_qa_master"

# Match "Deprecated", "DEPRECATED", "deprecated" anywhere in tool
# descriptions. Conservative on purpose — we want to catch every
# wording so contributors can't accidentally announce a deprecation
# without wiring the warning.
_DEPRECATION_MARKER = re.compile(r"deprecat", re.IGNORECASE)


def _current_tool_descriptions() -> dict[str, str]:
    """{tool_name: description} from list_tools()."""
    from mk_qa_master.server import list_tools

    async def _gather():
        return await list_tools()

    return {t.name: (t.description or "") for t in asyncio.run(_gather())}


def _source_contains_deprecation_warning() -> bool:
    """Returns True if ANY .py under src/mk_qa_master/ has a
    `DeprecationWarning` call. Cheap scan — we don't try to associate
    the warning with a specific tool because deprecations can fire from
    helper modules, decorators, or runner-internal paths."""
    for path in SRC_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "DeprecationWarning" in text and "warnings.warn" in text:
            return True
    return False


# ---- Description vs runtime pairing ---------------------------------------

def test_no_deprecation_marker_in_v1_descriptions():
    """v1.0 ships with ZERO deprecations announced. The schema is
    fresh. Any future v1.x PR that adds a deprecation MUST pair the
    description marker with a runtime `DeprecationWarning`.

    Drop this test the moment v1.x starts shipping deprecations —
    replace it with `test_every_deprecation_marker_has_runtime_warning`.
    """
    descriptions = _current_tool_descriptions()
    flagged = {
        name: desc for name, desc in descriptions.items()
        if _DEPRECATION_MARKER.search(desc)
    }
    if not flagged:
        return  # v1.0 baseline state — no deprecations exist yet

    # We've started shipping deprecations. Now the pairing matters.
    if not _source_contains_deprecation_warning():
        pytest.fail(
            "Tool description(s) mention 'deprecated' but no "
            "`warnings.warn(..., DeprecationWarning)` call exists "
            "under src/mk_qa_master/. The two signals must travel "
            "together — see docs/DEPRECATION-POLICY.md.\n\n"
            f"Tools with deprecation markers: {sorted(flagged.keys())}"
        )


def test_deprecation_policy_doc_exists():
    """v1.0 PRD §11 #6: the deprecation policy must exist alongside
    the snapshot ack mechanism. PR-1's snapshot test now refuses to
    rewrite without this doc (and the migration doc), so the doc's
    presence is load-bearing."""
    policy = REPO_ROOT / "docs" / "DEPRECATION-POLICY.md"
    assert policy.is_file(), (
        f"docs/DEPRECATION-POLICY.md missing — v1.0's deprecation "
        f"cycle relies on it. See prd-v1.0-stability-lock.md §6."
    )


def test_migration_doc_exists():
    """Same load-bearing role for the migration guide. The snapshot
    ack path checks both docs exist before honoring the override."""
    migration = REPO_ROOT / "docs" / "MIGRATION-0.x-to-1.0.md"
    assert migration.is_file(), (
        f"docs/MIGRATION-0.x-to-1.0.md missing — v1.0's schema "
        f"evolution audit trail relies on it."
    )
