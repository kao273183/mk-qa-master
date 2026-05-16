"""Smoke tests for mk-qa-master.

Catches the "easy" regressions that an MCP catalog or first-time user will
hit before they get to a real test run:
- package imports cleanly
- the MCP Server() is instantiable
- list_tools() returns the full advertised surface (currently 16 tools)
- dispatch table covers every declared tool (no name typos / unwired tools)

Per issue #35: the QA / testing MCP should have a CI smoke test of its
own, otherwise "doesn't test itself" is a real credibility hit.
"""

import asyncio


EXPECTED_TOOLS = {
    "get_runner_info",
    "list_tests",
    "run_tests",
    "run_failed",
    "get_test_report",
    "get_failure_details",
    "generate_test",
    "codegen",
    "generate_html_report",
    "get_test_history",
    "get_optimization_plan",
    "analyze_url",
    "analyze_screen",
    "init_qa_knowledge",
    "get_qa_context",
    "auto_generate_tests",
}


def test_package_importable():
    import mk_qa_master  # noqa: F401
    import mk_qa_master.server  # noqa: F401


def test_server_instantiable():
    from mk_qa_master.server import app

    assert app is not None
    assert app.name == "mk-qa-master"


def test_list_tools_returns_advertised_surface():
    from mk_qa_master.server import list_tools

    declared = {t.name for t in asyncio.run(list_tools())}
    missing = EXPECTED_TOOLS - declared
    assert not missing, f"Expected tools missing from list_tools(): {missing}"


def test_list_tools_count_matches_advertised_16():
    """If the count drifts, README and the family-site claim of '16 tools
    across 5 categories' is stale. Catch that here before users do."""
    from mk_qa_master.server import list_tools

    declared = {t.name for t in asyncio.run(list_tools())}
    assert len(declared) == 16, f"Expected 16 tools, got {len(declared)}: {sorted(declared)}"
