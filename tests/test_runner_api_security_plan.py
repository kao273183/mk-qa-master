"""v0.9.4 unit tests — plan_id integration on run_api_security_scan.

Mocks-only here; the real-HTTP dogfood lives in
`examples/sample_vulnerable_api/tests/test_runner_api_security_plan_dogfood.py`
to assert the end-to-end prelude→scan→verify shape against the Tier 1
vulnerable Flask app.
"""
from __future__ import annotations

import json

import pytest

from mk_qa_master.runners.api_security import run_scan
from mk_qa_master.tools.qa_plan import _reset_cache_for_tests, qa_plan_tool


@pytest.fixture
def consent(monkeypatch):
    monkeypatch.setenv("QA_API_SECURITY_CONSENT", "true")


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    _reset_cache_for_tests()
    monkeypatch.delenv("QA_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("QA_PLAN_PERSIST", raising=False)
    yield
    _reset_cache_for_tests()


MINIMAL_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Test", "version": "1"},
    "servers": [{"url": "http://localhost:9999"}],  # unreachable on purpose
    "paths": {
        "/health": {
            "get": {"operationId": "health",
                    "responses": {"200": {"description": "OK"}}},
        },
    },
}


@pytest.fixture
def spec_file(tmp_path):
    p = tmp_path / "spec.json"
    p.write_text(json.dumps(MINIMAL_SPEC))
    return str(p)


# ---- backward compat ---------------------------------------------------

def test_response_has_no_plan_verification_when_plan_id_omitted(
    consent, spec_file
):
    """v0.9.3 callers must NOT see the new field unless they ask."""
    result = run_scan(spec_file)
    assert "plan_verification" not in result


def test_run_scan_signature_accepts_plan_id_kwarg(consent, spec_file):
    """Sanity: passing plan_id=None must NOT add plan_verification
    (treat explicit None as "no plan")."""
    result = run_scan(spec_file, plan_id=None)
    assert "plan_verification" not in result


# ---- plan_verification surfacing ---------------------------------------

def test_run_scan_with_plan_id_includes_plan_verification(consent, spec_file):
    plan = qa_plan_tool({
        "task": "Confirm health endpoint",
        "critical_points": ["health endpoint reachable"],
    })
    result = run_scan(spec_file, plan_id=plan["plan_id"])
    assert "plan_verification" in result
    # No findings against unreachable localhost, so the CP can't match
    # → status failed. That's correct behavior.
    pv = result["plan_verification"]
    assert pv["plan_id"] == plan["plan_id"]
    assert pv["status"] == "failed"


def test_invalid_plan_id_surfaces_error_under_plan_verification(
    consent, spec_file
):
    """Scan still completes; plan_verification carries the error envelope."""
    result = run_scan(spec_file, plan_id="deadbeef0000")
    # The scan itself didn't error
    assert "error" not in result
    assert "findings" in result
    # But verify_plan inside it did
    assert result["plan_verification"]["error"] == "plan_not_found"


def test_plan_id_with_scan_error_does_not_double_up(consent, tmp_path):
    """Scan-level errors return the error envelope and skip the plan
    verify step entirely. Otherwise we'd surface 'plan_verification'
    on an error response, which would be confusing."""
    bad_spec_path = tmp_path / "nonexistent.yaml"
    plan = qa_plan_tool({"task": "x", "critical_points": ["y"]})
    result = run_scan(str(bad_spec_path), plan_id=plan["plan_id"])
    assert result["error"] == "spec_load_failed"
    assert "plan_verification" not in result


# ---- evidence flowing into verify --------------------------------------

def test_findings_above_threshold_passed_to_verify_plan(
    consent, monkeypatch, spec_file
):
    """Inject a synthesized HIGH finding and confirm the CP's hint
    matches against it through the plan verification step."""
    from mk_qa_master.security_rules import ALL_RULES
    from mk_qa_master.security_rules.base import Finding, Severity

    fake_findings = [
        Finding(
            rule_id="FAKE-API-RULE-Issue",
            severity=Severity.HIGH,
            endpoint="GET /health",
            title="injected_finding_for_test",
            evidence={"shape": "synthetic"},
        ),
    ]

    target_rule = ALL_RULES[0]
    monkeypatch.setattr(target_rule, "applies_to", lambda op: True)
    monkeypatch.setattr(
        target_rule, "execute",
        lambda client, op: fake_findings if op.path == "/health" else [],
    )

    plan = qa_plan_tool({
        "task": "Catch the injected finding",
        "critical_points": ["injected_finding_for_test"],
    })
    result = run_scan(spec_file, plan_id=plan["plan_id"],
                       severity_threshold="low")
    pv = result["plan_verification"]
    assert pv["status"] == "passed"
    assert pv["checklist"][0]["satisfied"]


def test_findings_below_threshold_invisible_to_verify(
    consent, monkeypatch, spec_file
):
    """The doc caveat in action — INFO finding doesn't satisfy a CP
    when severity_threshold='medium'."""
    from mk_qa_master.security_rules import ALL_RULES
    from mk_qa_master.security_rules.base import Finding, Severity

    info_finding = [
        Finding(
            rule_id="FAKE-INFO-NOTE",
            severity=Severity.INFO,  # below medium
            endpoint="GET /health",
            title="below_threshold_marker",
            evidence={},
        ),
    ]
    target_rule = ALL_RULES[0]
    monkeypatch.setattr(target_rule, "applies_to", lambda op: True)
    monkeypatch.setattr(target_rule, "execute",
                        lambda client, op: info_finding)

    plan = qa_plan_tool({
        "task": "x",
        "critical_points": ["below_threshold_marker"],
    })
    result = run_scan(spec_file, plan_id=plan["plan_id"],
                       severity_threshold="medium")
    pv = result["plan_verification"]
    # CP unmet because INFO finding was filtered out before verify saw it
    assert pv["status"] == "failed"


def test_findings_below_threshold_visible_when_threshold_lowered(
    consent, monkeypatch, spec_file
):
    """Same situation as above — but with severity_threshold='info'
    the finding does flow through and the CP matches."""
    from mk_qa_master.security_rules import ALL_RULES
    from mk_qa_master.security_rules.base import Finding, Severity

    info_finding = [
        Finding(
            rule_id="FAKE-INFO-NOTE",
            severity=Severity.INFO,
            endpoint="GET /health",
            title="info_marker_visible",
            evidence={},
        ),
    ]
    target_rule = ALL_RULES[0]
    monkeypatch.setattr(target_rule, "applies_to", lambda op: True)
    monkeypatch.setattr(target_rule, "execute",
                        lambda client, op: info_finding)

    plan = qa_plan_tool({
        "task": "x",
        "critical_points": ["info_marker_visible"],
    })
    result = run_scan(spec_file, plan_id=plan["plan_id"],
                       severity_threshold="info")
    pv = result["plan_verification"]
    assert pv["status"] == "passed"


# ---- response stays well-formed ---------------------------------------

def test_response_shape_locked(consent, spec_file):
    plan = qa_plan_tool({"task": "x", "critical_points": ["y"]})
    result = run_scan(spec_file, plan_id=plan["plan_id"])
    # All v0.8 fields still present
    for key in ("scan_id", "spec_url", "base_url", "categories_run",
                "rules_ran", "ops_scanned", "severity_threshold",
                "findings", "summary", "findings_below_threshold_count"):
        assert key in result, f"missing v0.8 field: {key}"
    # New v0.9.4 field
    assert "plan_verification" in result
    pv = result["plan_verification"]
    # plan_verification is a verify_plan response — locked schema
    for key in ("plan_id", "status", "checklist", "summary",
                "evidence_sources", "plan_source", "verified_at"):
        assert key in pv, f"missing verify_plan field in plan_verification: {key}"
