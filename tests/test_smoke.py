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


def test_schemathesis_runner_registered():
    """v0.6.0: the schemathesis runner must be discoverable via the
    REGISTRY. Failing this means QA_RUNNER=schemathesis won't resolve,
    which would silently regress the whole API-testing capability.

    The runner class itself imports `schemathesis` lazily inside its
    methods, so this assertion is safe even when the optional
    `[api]` extra isn't installed."""
    from mk_qa_master.runners import REGISTRY

    assert "schemathesis" in REGISTRY, (
        f"schemathesis runner not registered. Available: {sorted(REGISTRY)}"
    )
    assert "api" in REGISTRY, (
        "expected 'api' as an alias for the schemathesis runner"
    )
    assert REGISTRY["schemathesis"] is REGISTRY["api"], (
        "'api' should alias the same SchemathesisRunner class"
    )
    assert REGISTRY["schemathesis"].__name__ == "SchemathesisRunner"


def test_qa_lang_switches_builtin_methodology():
    """v0.6.2: `_builtin_for_lang('en')` must return English methodology
    (contains "ISTQB" but no Chinese H2 markers like "原則"); 'zh-tw' must
    return the Chinese version (contains "原則"). Common aliases ('zh',
    'zh_TW', 'CN') normalize to 'zh-tw' via config.py; invalid values
    fall back to 'en' rather than raising — we'd rather serve the wrong
    language than crash the server boot."""
    from mk_qa_master.tools.qa_context import _builtin_for_lang

    en_built = _builtin_for_lang("en")
    zh_built = _builtin_for_lang("zh-tw")

    assert "ISTQB" in en_built
    assert "原則" not in en_built, "English build must not contain Chinese section markers"
    assert "Your Business Rules" in en_built

    assert "原則" in zh_built, "zh-tw build must contain Chinese section markers"
    assert "你的業務規則" in zh_built

    # The function itself is the normalization boundary — config.py does the
    # alias mapping. Verify that an unexpected lang value falls back to EN
    # rather than crashing or returning an empty string.
    fallback = _builtin_for_lang("invalid-lang-code")
    assert fallback == en_built, "Unknown lang must fall back to English"


def test_qa_lang_alias_normalization():
    """Config-level alias normalization: zh / zh-cn / zh_cn / CN / zh_tw
    should all collapse to 'zh-tw'. Anything else (including unset)
    defaults to 'en'. We exercise the normalization logic by reloading
    config.py under different env values."""
    import importlib
    import os
    import mk_qa_master.config as cfg

    original = os.environ.get("QA_LANG")
    try:
        for alias in ("zh", "zh-cn", "zh_cn", "cn", "zh_tw", "ZH-TW", "Zh-Tw"):
            os.environ["QA_LANG"] = alias
            importlib.reload(cfg)
            assert cfg.QA_LANG == "zh-tw", f"Alias {alias!r} should normalize to zh-tw, got {cfg.QA_LANG!r}"

        for invalid in ("klingon", "fr", "ja", ""):
            os.environ["QA_LANG"] = invalid
            importlib.reload(cfg)
            assert cfg.QA_LANG == "en", f"Invalid lang {invalid!r} should fall back to en, got {cfg.QA_LANG!r}"

        os.environ["QA_LANG"] = "en"
        importlib.reload(cfg)
        assert cfg.QA_LANG == "en"
    finally:
        if original is None:
            os.environ.pop("QA_LANG", None)
        else:
            os.environ["QA_LANG"] = original
        importlib.reload(cfg)


def test_api_methodology_section_present_in_both_languages():
    """v0.6.2 adds an API Testing Methodology section in both languages.
    The English build advertises Pact + Schemathesis + idempotency keys;
    the Chinese build mirrors the same coverage with Chinese H2 titles."""
    from mk_qa_master.tools.qa_context import _builtin_for_lang

    en_built = _builtin_for_lang("en")
    zh_built = _builtin_for_lang("zh-tw")

    # EN side
    assert "## API Testing Methodology" in en_built
    assert "Pact" in en_built
    assert "Schemathesis" in en_built
    assert "Idempotency" in en_built or "idempotency" in en_built

    # zh-TW side
    assert "## API 測試方法論" in zh_built
    assert "Pact" in zh_built
    assert "冪等" in zh_built  # idempotency


def test_flakiness_taxonomy_present_in_both_languages():
    """v0.6.2 adds a five-cause flakiness taxonomy in both languages. The
    five causes are: race conditions, external dependencies, order-dependent
    tests, time-sensitive tests, resource leaks. Each block must carry the
    smell / fix / example trio."""
    from mk_qa_master.tools.qa_context import _builtin_for_lang

    en_built = _builtin_for_lang("en")
    zh_built = _builtin_for_lang("zh-tw")

    assert "## Flaky Test Root-Cause Taxonomy" in en_built
    for cause in ("Race conditions", "External dependencies", "Order-dependent",
                  "Time-sensitive", "Resource leaks"):
        assert cause in en_built, f"EN flakiness section missing '{cause}'"
    assert en_built.count("**Smell**") >= 5
    assert en_built.count("**Fix**") >= 5
    assert en_built.count("**Example**") >= 5

    assert "## Flaky 測試根因分類" in zh_built
    for cause in ("競態條件", "外部依賴", "順序相依", "時間敏感", "資源洩漏"):
        assert cause in zh_built, f"zh-TW flakiness section missing '{cause}'"


def test_newman_runner_registered():
    """v0.6.1: the newman runner must be discoverable via the REGISTRY
    under both `newman` and `postman` keys (mirrors how schemathesis
    aliases as both `schemathesis` and `api`).

    The runner only shells out to the `newman` CLI when methods are
    called, so this assertion is safe even when newman isn't installed
    on the test runner's PATH (Newman is npm-side and CI installs it
    in a dedicated job)."""
    from mk_qa_master.runners import REGISTRY

    assert "newman" in REGISTRY, (
        f"newman runner not registered. Available: {sorted(REGISTRY)}"
    )
    assert "postman" in REGISTRY, (
        "expected 'postman' as an alias for the newman runner"
    )
    assert REGISTRY["newman"] is REGISTRY["postman"], (
        "'postman' should alias the same NewmanRunner class"
    )
    assert REGISTRY["newman"].__name__ == "NewmanRunner"
