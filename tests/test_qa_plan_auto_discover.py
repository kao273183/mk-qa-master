"""v0.9.2 tests — verify_plan auto-discovery of evidence from
pytest-json-report.

Covers:
  - default off (backward-compat with v0.9.1)
  - on + report present → tests rows merged into evidence
  - on + explicit evidence → both sources combined
  - on + missing report → best-effort, no crash, sources reflects path
  - on + malformed report → same fallback
  - MK_QA_REPORT_PATH env override
  - explicit `report_path` arg overrides env
  - QA_PROJECT_ROOT path lookup
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mk_qa_master.tools.qa_plan import (
    _reset_cache_for_tests,
    qa_plan_tool,
    verify_plan_tool,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    _reset_cache_for_tests()
    yield
    _reset_cache_for_tests()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Make sure stray env vars from earlier tests don't bleed in."""
    monkeypatch.delenv("MK_QA_REPORT_PATH", raising=False)
    monkeypatch.delenv("QA_PROJECT_ROOT", raising=False)


def _write_report(path: Path, tests: list[dict]) -> Path:
    payload = {"summary": {"total": len(tests)}, "tests": tests}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _plan(critical_points: list) -> str:
    result = qa_plan_tool({
        "task": "Run my tests",
        "critical_points": critical_points,
    })
    return result["plan_id"]


# ---- Backward-compat: default OFF -----------------------------------------

def test_auto_discover_default_off_preserves_v091_behavior(tmp_path, monkeypatch):
    """Without auto_discover, verify_plan behaves exactly as v0.9.1.
    Even a report.json sitting in the configured project root must
    NOT leak into evidence unless asked."""
    # Plant a report.json that WOULD match the hint if discovered.
    report = _write_report(
        tmp_path / "report.json",
        [{"nodeid": "tests/test_login.py::test_valid", "outcome": "passed"}],
    )
    monkeypatch.setenv("MK_QA_REPORT_PATH", str(report))

    plan_id = _plan(["test_login_valid"])
    result = verify_plan_tool({
        "plan_id": plan_id,
        "evidence": [],  # explicit empty
    })
    assert result["status"] == "failed", \
        "auto_discover off + empty evidence must NOT silently pull from report.json"
    assert result["evidence_sources"]["autodiscovered"] is False


def test_omitting_both_evidence_and_auto_discover_errors():
    plan_id = _plan(["x"])
    result = verify_plan_tool({"plan_id": plan_id})
    assert result["error"] == "no_evidence"
    assert "auto_discover" in result["hint"]


# ---- Auto-discover ON: happy path ----------------------------------------

def test_auto_discover_pulls_pytest_report_tests_list(tmp_path, monkeypatch):
    report = _write_report(
        tmp_path / "report.json",
        [
            {"nodeid": "tests/test_login.py::test_login_valid",
             "outcome": "passed", "duration": 1.2},
            {"nodeid": "tests/test_login.py::test_login_expired_password",
             "outcome": "failed", "duration": 0.5},
        ],
    )
    monkeypatch.setenv("MK_QA_REPORT_PATH", str(report))

    plan_id = _plan(["test_login_valid", "test_login_expired_password"])
    result = verify_plan_tool({
        "plan_id": plan_id,
        "auto_discover": True,
    })

    assert "error" not in result
    assert result["status"] == "passed"
    assert result["evidence_sources"]["autodiscovered"] is True
    assert result["evidence_sources"]["autodiscovered_count"] == 2
    assert result["evidence_sources"]["explicit_count"] == 0
    assert result["evidence_sources"]["report_path"] == str(report)


def test_auto_discover_merges_with_explicit_evidence(tmp_path, monkeypatch):
    """When both sources are provided, BOTH should be searched."""
    report = _write_report(
        tmp_path / "report.json",
        [{"nodeid": "tests/test_login.py::test_login_valid",
          "outcome": "passed"}],
    )
    monkeypatch.setenv("MK_QA_REPORT_PATH", str(report))

    plan_id = _plan(["test_login_valid", "OWASP-API1-BOLA"])
    result = verify_plan_tool({
        "plan_id": plan_id,
        "evidence": [
            {"rule_id": "OWASP-API1-BOLA-CrossUserDataExposure",
             "severity": "critical"},
        ],
        "auto_discover": True,
    })
    assert result["status"] == "passed"
    assert result["evidence_sources"]["explicit_count"] == 1
    assert result["evidence_sources"]["autodiscovered_count"] == 1


def test_auto_discover_status_is_failed_when_report_doesnt_match_cps(
    tmp_path, monkeypatch
):
    """Auto-discovered rows present but none match the CPs → failed."""
    _write_report(
        tmp_path / "report.json",
        [{"nodeid": "tests/unrelated.py::test_other", "outcome": "passed"}],
    )
    monkeypatch.setenv("MK_QA_REPORT_PATH", str(tmp_path / "report.json"))

    plan_id = _plan(["test_login_valid"])
    result = verify_plan_tool({"plan_id": plan_id, "auto_discover": True})
    assert result["status"] == "failed"
    assert result["evidence_sources"]["autodiscovered_count"] == 1


# ---- Auto-discover ON: degraded sources -----------------------------------

def test_auto_discover_missing_report_does_not_crash(tmp_path, monkeypatch):
    """When the report file doesn't exist, auto_discover degrades to
    empty — no exception, no error envelope. Sources records the path
    we looked at so the user can diagnose."""
    missing = tmp_path / "no-report.json"
    monkeypatch.setenv("MK_QA_REPORT_PATH", str(missing))

    plan_id = _plan(["anything"])
    result = verify_plan_tool({"plan_id": plan_id, "auto_discover": True})
    assert "error" not in result
    assert result["status"] == "failed"  # zero evidence, zero satisfied
    assert result["evidence_sources"]["autodiscovered"] is False
    assert result["evidence_sources"]["report_path"] == str(missing.resolve())


def test_auto_discover_malformed_report_does_not_crash(tmp_path, monkeypatch):
    bad = tmp_path / "report.json"
    bad.write_text("{ not valid json at all", encoding="utf-8")
    monkeypatch.setenv("MK_QA_REPORT_PATH", str(bad))

    plan_id = _plan(["anything"])
    result = verify_plan_tool({"plan_id": plan_id, "auto_discover": True})
    assert "error" not in result
    assert result["evidence_sources"]["autodiscovered"] is False


def test_auto_discover_report_without_tests_list(tmp_path, monkeypatch):
    """Report exists but no `tests` array — surface gracefully."""
    weird = tmp_path / "report.json"
    weird.write_text(json.dumps({"summary": {}}), encoding="utf-8")
    monkeypatch.setenv("MK_QA_REPORT_PATH", str(weird))

    plan_id = _plan(["anything"])
    result = verify_plan_tool({"plan_id": plan_id, "auto_discover": True})
    assert "error" not in result
    assert result["evidence_sources"]["autodiscovered"] is False


def test_explicit_evidence_still_works_when_autodiscover_fails(
    tmp_path, monkeypatch
):
    """If auto-discover comes back empty, the explicit evidence still
    drives the verdict — don't drop it."""
    monkeypatch.setenv("MK_QA_REPORT_PATH", str(tmp_path / "absent.json"))

    plan_id = _plan(["scan finding"])
    result = verify_plan_tool({
        "plan_id": plan_id,
        "evidence": [{"finding": "scan finding present"}],
        "auto_discover": True,
    })
    assert result["status"] == "passed"
    assert result["evidence_sources"]["explicit_count"] == 1
    assert result["evidence_sources"]["autodiscovered"] is False


# ---- Path resolution priority --------------------------------------------

def test_explicit_report_path_overrides_env(tmp_path, monkeypatch):
    """`report_path` arg in the tool call wins over MK_QA_REPORT_PATH."""
    env_report = _write_report(
        tmp_path / "env.json",
        [{"nodeid": "irrelevant::test", "outcome": "passed"}],
    )
    arg_report = _write_report(
        tmp_path / "arg.json",
        [{"nodeid": "expected_match::test", "outcome": "passed"}],
    )
    monkeypatch.setenv("MK_QA_REPORT_PATH", str(env_report))

    plan_id = _plan(["expected_match"])
    result = verify_plan_tool({
        "plan_id": plan_id,
        "auto_discover": True,
        "report_path": str(arg_report),
    })
    assert result["status"] == "passed"
    assert result["evidence_sources"]["report_path"] == str(arg_report.resolve())


def test_mk_qa_report_path_env_honored(tmp_path, monkeypatch):
    report = _write_report(
        tmp_path / "custom.json",
        [{"nodeid": "tests/test_a.py::test_alpha", "outcome": "passed"}],
    )
    monkeypatch.setenv("MK_QA_REPORT_PATH", str(report))

    plan_id = _plan(["test_alpha"])
    result = verify_plan_tool({"plan_id": plan_id, "auto_discover": True})
    assert result["status"] == "passed"
    assert Path(result["evidence_sources"]["report_path"]) == report.resolve()


def test_qa_project_root_used_when_no_env_override(tmp_path, monkeypatch):
    """When MK_QA_REPORT_PATH is unset, fall back to
    <QA_PROJECT_ROOT>/report.json."""
    project_root = tmp_path / "myproj"
    report = _write_report(
        project_root / "report.json",
        [{"nodeid": "tests/test_x.py::test_proj_root_picked",
          "outcome": "passed"}],
    )
    monkeypatch.setenv("QA_PROJECT_ROOT", str(project_root))
    monkeypatch.delenv("MK_QA_REPORT_PATH", raising=False)

    plan_id = _plan(["test_proj_root_picked"])
    result = verify_plan_tool({"plan_id": plan_id, "auto_discover": True})
    assert result["status"] == "passed"
    assert Path(result["evidence_sources"]["report_path"]) == report.resolve()


# ---- Edge cases ----------------------------------------------------------

def test_auto_discover_with_outcome_conditional_hint(tmp_path, monkeypatch):
    """A CP can use `outcome` to assert pass vs fail."""
    _write_report(
        tmp_path / "report.json",
        [
            {"nodeid": "tests/test_x.py::test_a", "outcome": "passed"},
            {"nodeid": "tests/test_x.py::test_b", "outcome": "failed"},
        ],
    )
    monkeypatch.setenv("MK_QA_REPORT_PATH", str(tmp_path / "report.json"))

    plan_id = qa_plan_tool({
        "task": "run",
        "critical_points": [{
            "id": "CP1", "description": "test_a passes",
            "verification_hint": "test_a",  # alone — would match either
        }, {
            "id": "CP2", "description": "test_b explicitly failed",
            "verification_hint": "test_b outcome failed",
        }],
    })["plan_id"]
    result = verify_plan_tool({"plan_id": plan_id, "auto_discover": True})
    cp_by_id = {f["id"]: f for f in result["checklist"]}
    # CP1 matches "test_a" substring inside the nodeid
    assert cp_by_id["CP1"]["satisfied"]
    # CP2 requires both "test_b" AND "outcome failed" — the stringified
    # dict has `outcome failed` together, so it should match.
    assert cp_by_id["CP2"]["satisfied"]


def test_evidence_sources_shape_when_auto_discover_off():
    plan_id = _plan(["anything"])
    result = verify_plan_tool({"plan_id": plan_id, "evidence": ["x"]})
    # Backward-compat: response always carries evidence_sources now.
    assert result["evidence_sources"]["explicit_count"] == 1
    assert result["evidence_sources"]["autodiscovered"] is False
    assert result["evidence_sources"]["autodiscovered_count"] == 0
    assert result["evidence_sources"]["report_path"] is None
