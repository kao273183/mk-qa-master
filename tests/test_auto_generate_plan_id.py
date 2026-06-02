"""v0.10.0 PR-4 unit tests — plan_id integration on auto_generate_tests.

Theme A (Universal Bookend, prd-v0.10-universal-bookend.md §5.4).
Mocks analyze_url + generator.generate_test so the test can exercise
the auto-generate orchestration without launching a real browser
or writing files to PROJECT_ROOT.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from mk_qa_master.server import _auto_generate_tests
from mk_qa_master.tools.qa_plan import _reset_cache_for_tests, qa_plan_tool


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    _reset_cache_for_tests()
    monkeypatch.delenv("QA_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("QA_PLAN_PERSIST", raising=False)
    yield
    _reset_cache_for_tests()


@pytest.fixture
def fake_analysis_two_modules(monkeypatch):
    """Mock analyzer.analyze_url so the helper sees a known module set
    with one form (2 candidates) + one cta (1 candidate) — total 3
    test-generation calls when tests_per_module=2."""
    fake = {
        "url": "https://example.com",
        "page_title": "Example",
        "module_count": 2,
        "api_endpoint_count": 0,
        "modules": [
            {
                "kind": "form",
                "name": "signup_form_0",
                "selectors": {},
                "candidate_tcs": [
                    "送出空表單應顯示必填錯誤",
                    "輸入無效 email 應顯示格式錯誤",
                    "三筆 candidate — 跑 tests_per_module=2 時應只取前 2 條",
                ],
            },
            {
                "kind": "cta",
                "name": "primary_cta",
                "selectors": {},
                "candidate_tcs": ["按下主要按鈕應導向下一頁"],
            },
        ],
    }
    mock_analyze = AsyncMock(return_value=fake)
    monkeypatch.setattr(
        "mk_qa_master.tools.analyzer.analyze_url", mock_analyze
    )
    # Stub generator so generate_test does nothing destructive.
    monkeypatch.setattr(
        "mk_qa_master.tools.generator.generate_test",
        MagicMock(return_value=None),
    )
    # Telemetry stubs — same pattern.
    monkeypatch.setattr(
        "mk_qa_master.tools.telemetry.log_discovered_modules",
        MagicMock(),
    )
    monkeypatch.setattr(
        "mk_qa_master.tools.telemetry.log_generation",
        MagicMock(),
    )


@pytest.fixture
def fake_analysis_with_generation_failure(monkeypatch):
    """Like fake_analysis_two_modules, but generate_test raises on the
    second call so we can assert error-record evidence."""
    fake = {
        "url": "https://example.com",
        "page_title": "Example",
        "module_count": 1,
        "api_endpoint_count": 0,
        "modules": [
            {
                "kind": "form",
                "name": "signup_form_0",
                "selectors": {},
                "candidate_tcs": ["tc one", "tc two"],
            },
        ],
    }
    monkeypatch.setattr(
        "mk_qa_master.tools.analyzer.analyze_url",
        AsyncMock(return_value=fake),
    )

    call_count = {"n": 0}

    def flaky_generate(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:  # second call fails
            raise RuntimeError("ImaginarySDK error: rate-limited")
        return None

    monkeypatch.setattr(
        "mk_qa_master.tools.generator.generate_test", flaky_generate
    )
    monkeypatch.setattr(
        "mk_qa_master.tools.telemetry.log_discovered_modules",
        MagicMock(),
    )
    monkeypatch.setattr(
        "mk_qa_master.tools.telemetry.log_generation",
        MagicMock(),
    )


# ---------------------------------------------------------------------------
# Backward compat
# ---------------------------------------------------------------------------

def test_auto_generate_without_plan_id_unchanged(fake_analysis_two_modules):
    """Legacy callers must not see plan_verification when plan_id is
    omitted. The v0.9.5 shape stays intact."""
    result = asyncio.run(_auto_generate_tests(
        url="https://example.com",
        timeout_ms=1000,
        auth_cookie=None,
        tests_per_module=2,
    ))
    assert "plan_verification" not in result
    assert result["tests_generated"] == 3  # form (2) + cta (1)


# ---------------------------------------------------------------------------
# Evidence shape (§5.4)
# ---------------------------------------------------------------------------

def test_auto_generate_with_plan_id_emits_one_row_per_file(
    fake_analysis_two_modules,
):
    """Each generated test record (success or failure) becomes ONE
    evidence row with the documented §5.4 shape."""
    plan = qa_plan_tool({
        "task": "Cover example.com",
        "critical_points": [
            {
                "kind": "happy_path",
                "description": "form module covered",
                "verification_hint": "form",
            },
            {
                "kind": "happy_path",
                "description": "cta module covered",
                "verification_hint": "cta",
            },
        ],
    })

    result = asyncio.run(_auto_generate_tests(
        url="https://example.com",
        timeout_ms=1000,
        auth_cookie=None,
        tests_per_module=2,
        plan_id=plan["plan_id"],
    ))

    assert "plan_verification" in result
    pv = result["plan_verification"]
    assert pv["status"] == "passed", f"both CPs should resolve; got {pv}"

    # Inspect the evidence rows attached to the satisfied CPs.
    matched_form = pv["checklist"][0]["matched_evidence"]
    matched_cta = pv["checklist"][1]["matched_evidence"]

    # form has 2 tests generated (tests_per_module=2).
    assert len(matched_form) == 2
    for row in matched_form:
        assert row["kind"] == "generated_test"
        assert row["covers_module"] == "form"
        assert row["error"] is None
        assert row["url"] == "https://example.com"
        assert row["path"].startswith("test_")

    # cta has 1 test generated.
    assert len(matched_cta) == 1
    cta_row = matched_cta[0]
    assert cta_row["kind"] == "generated_test"
    assert cta_row["covers_module"] == "cta"


def test_auto_generate_module_covered_cp_matches_kind(
    fake_analysis_two_modules,
):
    """A CP looking for `cta` coverage resolves because at least one
    evidence row has covers_module=cta."""
    plan = qa_plan_tool({
        "task": "Just cta coverage",
        "critical_points": [
            {
                "kind": "happy_path",
                "description": "cta covered",
                "verification_hint": "cta",
            },
        ],
    })

    result = asyncio.run(_auto_generate_tests(
        url="https://example.com",
        timeout_ms=1000,
        auth_cookie=None,
        tests_per_module=1,
        plan_id=plan["plan_id"],
    ))

    pv = result["plan_verification"]
    assert pv["status"] == "passed"


def test_auto_generate_unknown_module_cp_unmet(fake_analysis_two_modules):
    """CP for a module kind not produced (e.g., tab_bar — only form +
    cta are mocked) returns unsatisfied, not error."""
    plan = qa_plan_tool({
        "task": "Want tab_bar",
        "critical_points": [
            {
                "kind": "happy_path",
                "description": "tab_bar tests generated",
                "verification_hint": "tab_bar",
            },
        ],
    })

    result = asyncio.run(_auto_generate_tests(
        url="https://example.com",
        timeout_ms=1000,
        auth_cookie=None,
        tests_per_module=1,
        plan_id=plan["plan_id"],
    ))

    pv = result["plan_verification"]
    assert "error" not in pv
    assert pv["status"] in ("failed", "incomplete")
    assert pv["checklist"][0]["satisfied"] is False


# ---------------------------------------------------------------------------
# Generation failure paths
# ---------------------------------------------------------------------------

def test_auto_generate_generation_failure_flows_into_evidence(
    fake_analysis_with_generation_failure,
):
    """When generate_test raises, the failure record still flows into
    evidence (with `error` populated). CPs can assert on either
    success-mode invariants ('all generations succeeded') or failure-mode
    ones ('at least one failure logged')."""
    plan = qa_plan_tool({
        "task": "Generation should fail",
        "critical_points": [
            {
                "kind": "happy_path",
                "description": "captures generation error",
                "verification_hint": "ImaginarySDK",
            },
        ],
    })

    result = asyncio.run(_auto_generate_tests(
        url="https://example.com",
        timeout_ms=1000,
        auth_cookie=None,
        tests_per_module=2,
        plan_id=plan["plan_id"],
    ))

    pv = result["plan_verification"]
    # The error message contains "ImaginarySDK" → CP resolves.
    assert pv["status"] == "passed", (
        f"expected the error-substring CP to match; got {pv}"
    )

    # The matched evidence row carries error=<error string>, kind=generated_test.
    matched = pv["checklist"][0]["matched_evidence"]
    assert any(
        row.get("error") and "ImaginarySDK" in row["error"] for row in matched
    ), f"expected error row in evidence; got {matched}"


# ---------------------------------------------------------------------------
# verify_plan error envelope surfacing
# ---------------------------------------------------------------------------

def test_auto_generate_with_unknown_plan_id_surfaces_plan_not_found(
    fake_analysis_two_modules,
):
    """When plan_id points to a non-existent plan, verify_plan errors
    surface UNDER plan_verification — the generation itself completed."""
    result = asyncio.run(_auto_generate_tests(
        url="https://example.com",
        timeout_ms=1000,
        auth_cookie=None,
        tests_per_module=1,
        plan_id="deadbeef0000",
    ))

    # Generation completed normally.
    assert result["tests_generated"] >= 1
    # But plan_verification carries the error envelope.
    assert "plan_verification" in result
    assert result["plan_verification"].get("error") == "plan_not_found"


# ---------------------------------------------------------------------------
# analyze_url error path skips verification
# ---------------------------------------------------------------------------

def test_auto_generate_with_analyze_url_error_skips_verification(monkeypatch):
    """When analyze_url itself errors (unreachable URL etc.), the
    helper returns the error envelope unchanged. plan_verification
    MUST NOT appear — there are no modules / tests to verify against."""
    monkeypatch.setattr(
        "mk_qa_master.tools.analyzer.analyze_url",
        AsyncMock(return_value={"error": "打開頁面失敗", "url": "x"}),
    )
    result = asyncio.run(_auto_generate_tests(
        url="https://unreachable.invalid",
        timeout_ms=100,
        auth_cookie=None,
        tests_per_module=1,
        plan_id="any-plan-id",
    ))
    assert "error" in result
    assert "plan_verification" not in result
