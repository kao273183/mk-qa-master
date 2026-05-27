"""Unit tests for the v0.9.1 qa_plan + verify_plan tools.

Plans store critical points (CPs); verify walks them against evidence.
Tests cover input validation, TTL expiry, LRU eviction, evidence
matching semantics, and the status-derivation rules.
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest

from mk_qa_master.tools import qa_plan
from mk_qa_master.tools.qa_plan import (
    _CACHE_MAX,
    _CACHE_TTL_SECONDS,
    _ACTIVE_PLANS,
    _reset_cache_for_tests,
    qa_plan_tool,
    verify_plan_tool,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    _reset_cache_for_tests()
    yield
    _reset_cache_for_tests()


# ---- qa_plan_tool — input validation -----------------------------------

def test_qa_plan_creates_plan_with_id():
    result = qa_plan_tool({
        "task": "Run the login regression tests",
        "critical_points": [
            "test_login_with_valid_credentials passes",
            "test_login_with_expired_password rejects with 401",
        ],
    })
    assert "error" not in result
    assert isinstance(result["plan_id"], str)
    assert len(result["plan_id"]) == 12
    assert result["task"] == "Run the login regression tests"
    assert result["kind"] is None
    assert len(result["critical_points"]) == 2
    # IDs auto-assigned
    assert result["critical_points"][0]["id"] == "CP1"
    assert result["critical_points"][1]["id"] == "CP2"


def test_qa_plan_rejects_empty_task():
    result = qa_plan_tool({"task": "", "critical_points": ["x"]})
    assert result["error"] == "no_task"


def test_qa_plan_rejects_missing_task():
    result = qa_plan_tool({"critical_points": ["x"]})
    assert result["error"] == "no_task"


def test_qa_plan_rejects_empty_critical_points():
    result = qa_plan_tool({"task": "do something", "critical_points": []})
    assert result["error"] == "no_critical_points"


def test_qa_plan_rejects_missing_critical_points():
    result = qa_plan_tool({"task": "do something"})
    assert result["error"] == "no_critical_points"


def test_qa_plan_rejects_non_list_critical_points():
    result = qa_plan_tool({"task": "x", "critical_points": "CP1: do thing"})
    assert result["error"] == "no_critical_points"


def test_qa_plan_rejects_dict_cp_without_description():
    result = qa_plan_tool({
        "task": "x",
        "critical_points": [{"id": "CP1", "verification_hint": "foo"}],
    })
    assert result["error"] == "bad_critical_points"
    assert "description" in result["hint"]


def test_qa_plan_rejects_duplicate_cp_ids():
    result = qa_plan_tool({
        "task": "x",
        "critical_points": [
            {"id": "CP1", "description": "first"},
            {"id": "CP1", "description": "second"},
        ],
    })
    assert result["error"] == "bad_critical_points"
    assert "duplicate" in result["hint"]


def test_qa_plan_rejects_unknown_kind():
    result = qa_plan_tool({
        "task": "x", "kind": "frob",
        "critical_points": ["y"],
    })
    assert result["error"] == "bad_kind"


@pytest.mark.parametrize("kind", ["run", "generate", "scan", "debug", "captcha"])
def test_qa_plan_accepts_all_valid_kinds(kind):
    result = qa_plan_tool({
        "task": "x", "kind": kind,
        "critical_points": ["y"],
    })
    assert "error" not in result
    assert result["kind"] == kind


def test_qa_plan_kind_normalized_to_lowercase():
    result = qa_plan_tool({
        "task": "x", "kind": "RUN",
        "critical_points": ["y"],
    })
    assert result["kind"] == "run"


def test_qa_plan_string_cps_use_description_as_verification_hint():
    """For string-form CPs (the most common case), description and hint
    are the same — keeps the auto-matching predictable."""
    result = qa_plan_tool({
        "task": "x",
        "critical_points": ["login regression passes"],
    })
    cp = result["critical_points"][0]
    assert cp["description"] == "login regression passes"
    assert cp["verification_hint"] == "login regression passes"


def test_qa_plan_dict_cp_can_override_verification_hint():
    result = qa_plan_tool({
        "task": "x",
        "critical_points": [{
            "description": "All login tests pass",
            "verification_hint": "test_login",  # narrower substring
        }],
    })
    cp = result["critical_points"][0]
    assert cp["description"] == "All login tests pass"
    assert cp["verification_hint"] == "test_login"


# ---- TTL + cache size --------------------------------------------------

def test_plan_appears_in_active_cache_after_creation():
    result = qa_plan_tool({"task": "x", "critical_points": ["y"]})
    assert result["plan_id"] in _ACTIVE_PLANS


def test_plan_evicted_after_ttl():
    """Mock _now to advance past TTL."""
    result = qa_plan_tool({"task": "x", "critical_points": ["y"]})
    plan_id = result["plan_id"]

    # First call works (no plan_not_found before TTL elapses)
    verify = verify_plan_tool({"plan_id": plan_id, "evidence": []})
    assert verify.get("error") != "plan_not_found"

    # Now advance time past TTL and the plan should be gone
    with patch.object(qa_plan, "_now") as fake_now:
        from datetime import datetime, timezone
        fake_now.return_value = datetime.now(timezone.utc) + timedelta(
            seconds=_CACHE_TTL_SECONDS + 60
        )
        verify_after = verify_plan_tool({"plan_id": plan_id, "evidence": []})
        assert verify_after["error"] == "plan_not_found"


def test_lru_evicts_oldest_when_cap_reached():
    """Push CACHE_MAX + 5 plans, the first 5 should be gone."""
    first_ids: list[str] = []
    for i in range(_CACHE_MAX + 5):
        result = qa_plan_tool({
            "task": f"task {i}",
            "critical_points": [f"cp {i}"],
        })
        if i < 5:
            first_ids.append(result["plan_id"])

    for stale_id in first_ids:
        verify = verify_plan_tool({"plan_id": stale_id, "evidence": []})
        assert verify["error"] == "plan_not_found"


# ---- verify_plan_tool — basic happy path -------------------------------

def test_verify_plan_all_satisfied_returns_passed():
    plan = qa_plan_tool({
        "task": "Run smoke",
        "critical_points": ["login passes", "checkout passes"],
    })
    verify = verify_plan_tool({
        "plan_id": plan["plan_id"],
        "evidence": [
            "test_smoke_login passes in 1.2s",
            "test_smoke_checkout passes in 0.8s",
        ],
    })
    assert verify["status"] == "passed"
    assert verify["summary"]["satisfied"] == 2
    assert verify["unmet"] == []


def test_verify_plan_partial_returns_incomplete():
    plan = qa_plan_tool({
        "task": "Run smoke",
        "critical_points": ["login passes", "checkout passes"],
    })
    verify = verify_plan_tool({
        "plan_id": plan["plan_id"],
        "evidence": ["test_smoke_login passes"],  # no checkout evidence
    })
    assert verify["status"] == "incomplete"
    assert verify["summary"]["satisfied"] == 1
    assert verify["summary"]["unsatisfied"] == 1
    assert verify["unmet"] == ["CP2"]


def test_verify_plan_zero_matched_returns_failed():
    plan = qa_plan_tool({
        "task": "Run smoke",
        "critical_points": ["login passes", "checkout passes"],
    })
    verify = verify_plan_tool({
        "plan_id": plan["plan_id"],
        "evidence": ["unrelated event happened"],
    })
    assert verify["status"] == "failed"
    assert verify["unmet"] == ["CP1", "CP2"]


def test_verify_plan_empty_evidence_returns_failed():
    plan = qa_plan_tool({
        "task": "x",
        "critical_points": ["y"],
    })
    verify = verify_plan_tool({"plan_id": plan["plan_id"], "evidence": []})
    assert verify["status"] == "failed"


# ---- verify_plan_tool — input validation -------------------------------

def test_verify_plan_rejects_missing_plan_id():
    result = verify_plan_tool({"evidence": []})
    assert result["error"] == "no_plan_id"


def test_verify_plan_rejects_unknown_plan_id():
    result = verify_plan_tool({"plan_id": "deadbeef0000", "evidence": []})
    assert result["error"] == "plan_not_found"


def test_verify_plan_rejects_missing_evidence_field():
    plan = qa_plan_tool({"task": "x", "critical_points": ["y"]})
    result = verify_plan_tool({"plan_id": plan["plan_id"]})
    assert result["error"] == "no_evidence"


def test_verify_plan_rejects_non_list_evidence():
    plan = qa_plan_tool({"task": "x", "critical_points": ["y"]})
    result = verify_plan_tool({"plan_id": plan["plan_id"], "evidence": "y"})
    assert result["error"] == "bad_evidence"


# ---- evidence matching semantics --------------------------------------

def test_match_is_case_insensitive():
    """Hint 'TEST_LOGIN' (uppercase) must match evidence containing
    'test_login' (lowercase). Same substring, different case."""
    plan = qa_plan_tool({
        "task": "x",
        "critical_points": ["TEST_LOGIN"],
    })
    verify = verify_plan_tool({
        "plan_id": plan["plan_id"],
        "evidence": ["test_login passes"],
    })
    assert verify["status"] == "passed"


def test_match_searches_inside_dict_evidence():
    plan = qa_plan_tool({
        "task": "x",
        "critical_points": [{"description": "BOLA finding",
                             "verification_hint": "OWASP-API1-BOLA"}],
    })
    verify = verify_plan_tool({
        "plan_id": plan["plan_id"],
        "evidence": [
            {"rule_id": "OWASP-API1-BOLA-CrossUserDataExposure",
             "severity": "critical", "endpoint": "GET /orders/{id}"},
        ],
    })
    assert verify["status"] == "passed"
    cp = verify["checklist"][0]
    assert cp["satisfied"]
    assert len(cp["matched_evidence"]) == 1


def test_match_searches_inside_nested_dict_evidence():
    plan = qa_plan_tool({
        "task": "x",
        "critical_points": [{"description": "alice reads bob",
                             "verification_hint": "bob's pizza"}],
    })
    verify = verify_plan_tool({
        "plan_id": plan["plan_id"],
        "evidence": [{
            "rule_id": "OWASP-API1",
            "evidence": {"response_body_preview": "{\"item\": \"bob's pizza\"}"},
        }],
    })
    assert verify["status"] == "passed"


def test_match_searches_inside_list_evidence():
    plan = qa_plan_tool({
        "task": "x",
        "critical_points": [{"description": "tag check",
                             "verification_hint": "@security"}],
    })
    verify = verify_plan_tool({
        "plan_id": plan["plan_id"],
        "evidence": [{"tags": ["smoke", "@security", "regression"]}],
    })
    assert verify["status"] == "passed"


def test_returned_checklist_includes_full_cp_record():
    plan = qa_plan_tool({
        "task": "x",
        "critical_points": [{
            "id": "CUSTOM-1",
            "description": "Detailed CP description",
            "verification_hint": "passing",
        }],
    })
    verify = verify_plan_tool({
        "plan_id": plan["plan_id"],
        "evidence": ["test xyz is passing"],
    })
    cp = verify["checklist"][0]
    assert cp["id"] == "CUSTOM-1"
    assert cp["description"] == "Detailed CP description"
    assert cp["verification_hint"] == "passing"
    assert cp["satisfied"]
    assert cp["matched_evidence"] == ["test xyz is passing"]


def test_verify_returns_verified_at_timestamp():
    plan = qa_plan_tool({"task": "x", "critical_points": ["y"]})
    verify = verify_plan_tool({"plan_id": plan["plan_id"], "evidence": ["y"]})
    # ISO 8601-ish; just check it parses-ish
    assert "T" in verify["verified_at"]


# ---- evidence-format edge cases ---------------------------------------

def test_evidence_can_mix_strings_dicts_lists():
    plan = qa_plan_tool({
        "task": "x",
        "critical_points": ["one matches", "two matches"],
    })
    verify = verify_plan_tool({
        "plan_id": plan["plan_id"],
        "evidence": [
            "one matches indeed",
            {"deep": {"text": "two matches and more"}},
        ],
    })
    assert verify["status"] == "passed"


def test_primitive_int_evidence_handled_gracefully():
    """Don't crash if the host passes a primitive."""
    plan = qa_plan_tool({"task": "x", "critical_points": ["404"]})
    verify = verify_plan_tool({"plan_id": plan["plan_id"], "evidence": [404]})
    assert verify["status"] == "passed"
