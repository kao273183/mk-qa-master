"""v0.10.0 PR-1 unit tests — plan_id integration on run_tests.

Theme A (Universal Bookend, prd-v0.10-universal-bookend.md). Mirrors the
shape established by `tests/test_runner_api_security_plan.py` so the
two integrations share a coverage idiom.

The underlying test runner is mocked — we don't want unit tests to
actually spin up pytest. Verification flows through report.json on
disk, which we materialize in tmp paths via `MK_QA_REPORT_PATH`.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from mk_qa_master.tools import runner as runner_tool
from mk_qa_master.tools.qa_plan import _reset_cache_for_tests, qa_plan_tool


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    _reset_cache_for_tests()
    monkeypatch.delenv("QA_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("QA_PLAN_PERSIST", raising=False)
    monkeypatch.delenv("MK_QA_REPORT_PATH", raising=False)
    yield
    _reset_cache_for_tests()


@pytest.fixture
def fake_runner(monkeypatch):
    """Swap out the real test runner with a MagicMock so unit tests
    don't spawn pytest subprocesses. The mock's `run_tests` returns a
    realistic-looking result dict."""
    mock = MagicMock()
    mock.run_tests.return_value = {
        "exit_code": 0,
        "raw_exit_code": 0,
        "stdout_tail": "...",
        "stderr_tail": "",
    }
    monkeypatch.setattr(
        "mk_qa_master.tools.runner.get_runner", lambda: mock
    )
    return mock


@pytest.fixture
def report_path_with_tests(tmp_path, monkeypatch):
    """Materialize a pytest-json-report-shaped report.json under a tmp
    location and point MK_QA_REPORT_PATH at it. Returns the path."""
    report = {
        "summary": {"total": 2, "passed": 1, "failed": 1},
        "duration": 0.5,
        "tests": [
            {
                "nodeid": "tests/test_signup.py::test_happy_path",
                "outcome": "passed",
                "duration": 0.234,
                "call": {"duration": 0.234, "longrepr": None},
            },
            {
                "nodeid": "tests/test_signup.py::test_bad_email",
                "outcome": "failed",
                "duration": 0.156,
                "call": {
                    "duration": 0.156,
                    "longrepr": "AssertionError: expected 400",
                },
            },
        ],
    }
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setenv("MK_QA_REPORT_PATH", str(path))
    return path


# ---- backward compat (decision §11 #6 — parametrized later) ---------------

def test_run_tests_without_plan_id_unchanged(fake_runner):
    """Legacy callers must not see `plan_verification` unless they
    threaded plan_id through. v0.9.5 contract preserved."""
    result = runner_tool.run_tests()
    assert "plan_verification" not in result
    assert result["exit_code"] == 0


def test_run_tests_with_plan_id_none_is_same_as_omitted(fake_runner):
    """`plan_id=None` is the documented escape-hatch for callers that
    want to thread the arg through unconditionally without opting in."""
    result = runner_tool.run_tests(plan_id=None)
    assert "plan_verification" not in result


# ---- plan_verification surfacing -----------------------------------------

def test_run_tests_with_plan_id_attaches_plan_verification(
    fake_runner, report_path_with_tests
):
    """Happy path: plan exists, report.json on disk, verification gets
    attached under `plan_verification`."""
    plan = qa_plan_tool({
        "task": "Smoke the signup flow",
        "critical_points": [
            {
                "kind": "happy_path",
                "description": "happy path passes",
                "verification_hint": "test_happy_path",
            },
        ],
    })

    result = runner_tool.run_tests(plan_id=plan["plan_id"])

    assert "plan_verification" in result
    pv = result["plan_verification"]
    assert pv["plan_id"] == plan["plan_id"]
    assert "checklist" in pv
    # The happy-path CP should resolve as satisfied because the test
    # row from report.json matches the verification_hint.
    cps = pv["checklist"]
    assert any(cp.get("satisfied") is True for cp in cps), (
        f"expected at least one satisfied CP; got {cps}"
    )


def test_run_tests_evidence_sourced_from_report_json(
    fake_runner, report_path_with_tests
):
    """v0.9.2 evidence_sources should mark the verification as
    `autodiscovered` (we passed auto_discover=True, no explicit evidence)."""
    plan = qa_plan_tool({
        "task": "Check signup",
        "critical_points": [
            {
                "kind": "happy_path",
                "description": "h",
                "verification_hint": "test_happy_path",
            },
        ],
    })

    result = runner_tool.run_tests(plan_id=plan["plan_id"])

    sources = result["plan_verification"].get("evidence_sources", {})
    # We passed auto_discover=True without explicit evidence; v0.9.2's
    # evidence_sources should mark autodiscovered=True and explicit_count=0.
    assert sources.get("autodiscovered") is True, (
        f"expected autodiscovered=True; got {sources}"
    )
    assert sources.get("explicit_count") == 0, (
        f"expected explicit_count=0; got {sources}"
    )
    # And the autodiscovery actually found rows.
    assert sources.get("autodiscovered_count", 0) > 0, (
        f"expected nonzero autodiscovered_count; got {sources}"
    )


# ---- error envelopes ------------------------------------------------------

def test_run_tests_with_unknown_plan_id_surfaces_plan_not_found(
    fake_runner, report_path_with_tests
):
    """The scan ran fine — verify_plan's `plan_not_found` envelope gets
    surfaced under `plan_verification`, not raised as a runner error."""
    result = runner_tool.run_tests(plan_id="deadbeef0000")

    assert result["exit_code"] == 0  # the test run itself was fine
    assert "plan_verification" in result
    assert result["plan_verification"].get("error") == "plan_not_found"


def test_run_tests_with_no_report_json_yields_evidence_empty(
    fake_runner, monkeypatch, tmp_path
):
    """No report.json on disk → auto_discover returns empty evidence →
    every CP shows unsatisfied (no error)."""
    # Point at a nonexistent report path.
    monkeypatch.setenv("MK_QA_REPORT_PATH", str(tmp_path / "no-such.json"))

    plan = qa_plan_tool({
        "task": "Check",
        "critical_points": [
            {
                "kind": "happy_path",
                "description": "h",
                "verification_hint": "test_x",
            },
        ],
    })

    result = runner_tool.run_tests(plan_id=plan["plan_id"])

    assert "plan_verification" in result
    pv = result["plan_verification"]
    # No hard error — the scan ran fine, just no evidence to match
    # against. status reflects "no CPs satisfied".
    assert "error" not in pv, f"unexpected error: {pv}"
    assert pv["status"] in ("failed", "incomplete"), (
        f"expected failed/incomplete on empty evidence; got {pv}"
    )


# ---- error path on runner ------------------------------------------------

def test_run_tests_with_runner_error_skips_plan_verification(monkeypatch):
    """When the runner itself returns `{error: ...}` (e.g., blocked by
    security filter), we MUST NOT call verify_plan — the run never
    happened, there's no report.json to verify against."""
    # Stub the runner to return an error envelope.
    mock = MagicMock()
    monkeypatch.setattr(
        "mk_qa_master.tools.runner.get_runner", lambda: mock
    )
    # validate_filter blocks "../etc/passwd" via the security guardrail
    # — that returns {"error": ...} before get_runner() is even called.
    result = runner_tool.run_tests(
        filter="../etc/passwd", plan_id="some-plan-id"
    )

    assert "error" in result
    assert "plan_verification" not in result, (
        "runner errored — verification path must not have fired"
    )
