"""v0.10.0 PR-2 unit tests — plan_id integration on solve_visual_challenge.

Theme A (Universal Bookend, prd-v0.10-universal-bookend.md). Mirrors
PR-1's shape (tests/test_runner_tool_plan_id.py). The hard rule from
§11 #2 — raw token NEVER in evidence — gets its own dedicated test
because credential leakage would be the worst-shaped regression we
could ship.
"""
from __future__ import annotations

import importlib
import json

import pytest

from mk_qa_master.tools.qa_plan import _reset_cache_for_tests, qa_plan_tool


# ---------------------------------------------------------------------------
# Shared fixtures — reuse the visual_challenge mock infrastructure from the
# existing test file so we don't reinvent the mock-page shape.
# ---------------------------------------------------------------------------

def _reload_modules_with_env(monkeypatch, **env):
    """Reload config + visual_challenge with the supplied env. Returns the
    fresh visual_challenge module — tests bind to *this* reference, not
    the one imported at file top, so each test sees its own gate values."""
    for k in (
        "QA_VISUAL_CHALLENGE_CONSENT",
        "QA_VISUAL_CHALLENGE_TIMEOUT",
        "QA_VISUAL_CHALLENGE_AUTHORIZED_DOMAINS",
    ):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    import mk_qa_master.config as cfg
    importlib.reload(cfg)

    from mk_qa_master.tools import visual_challenge as vc
    importlib.reload(vc)
    vc._reset_cache_for_tests()
    return vc


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    _reset_cache_for_tests()
    monkeypatch.delenv("QA_PLAN_PERSIST", raising=False)
    monkeypatch.delenv("QA_PROJECT_ROOT", raising=False)
    yield
    _reset_cache_for_tests()


@pytest.fixture
def vc(monkeypatch):
    """visual_challenge module with consent enabled."""
    return _reload_modules_with_env(
        monkeypatch, QA_VISUAL_CHALLENGE_CONSENT="true"
    )


@pytest.fixture
def page():
    """Mock playwright page that returns a fake token on solve."""
    from tests.test_visual_challenge import _make_mock_page
    return _make_mock_page()


# ---------------------------------------------------------------------------
# Backward compat
# ---------------------------------------------------------------------------

def test_solve_without_plan_id_unchanged(vc, page):
    """Legacy callers must not see `plan_verification`."""
    inspected = vc.inspect_visual_challenge_tool({"_page": page})
    out = vc.solve_visual_challenge_tool({
        "challenge_id": inspected["challenge_id"],
        "selected_tile_indices": [0],
        "confirm": True,
    })
    assert out["status"] == "passed"
    assert "plan_verification" not in out


# ---------------------------------------------------------------------------
# Happy path — plan_verification surfacing
# ---------------------------------------------------------------------------

def test_solve_with_plan_id_emits_captcha_solve_record(vc, page):
    """The single-record evidence has every field documented in
    prd-v0.10-universal-bookend.md §5.2."""
    plan = qa_plan_tool({
        "task": "Solve signup captcha",
        "critical_points": [
            {
                "kind": "happy_path",
                "description": "captcha solved with token",
                "verification_hint": "passed",
            },
        ],
    })

    inspected = vc.inspect_visual_challenge_tool({"_page": page})
    out = vc.solve_visual_challenge_tool({
        "challenge_id": inspected["challenge_id"],
        "selected_tile_indices": [0],
        "confirm": True,
        "plan_id": plan["plan_id"],
    })

    assert out["status"] == "passed"
    assert "plan_verification" in out
    pv = out["plan_verification"]
    assert pv["plan_id"] == plan["plan_id"]

    # Evidence is a single record with the documented shape.
    cp0 = pv["checklist"][0]
    assert cp0["satisfied"] is True
    matched = cp0["matched_evidence"]
    assert len(matched) == 1
    record = matched[0]
    assert record["kind"] == "captcha_solve"
    assert record["status"] == "passed"
    assert record["token_populated"] is True
    assert "rounds_used" in record
    assert "fingerprint" in record
    assert "challenge_id" in record


def test_solve_with_plan_id_excludes_raw_token_from_evidence(vc, page):
    """§11 #2 hard rule: the raw `token` field must NEVER appear in
    evidence. Only `token_populated: bool`. Honors v0.7.0 telemetry
    hygiene NFR — putting the credential into evidence would route it
    into verify_plan's disk-persistence path."""
    plan = qa_plan_tool({
        "task": "Solve captcha",
        "critical_points": [
            {
                "kind": "happy_path",
                "description": "captcha solved",
                "verification_hint": "captcha_solve",
            },
        ],
    })

    inspected = vc.inspect_visual_challenge_tool({"_page": page})
    out = vc.solve_visual_challenge_tool({
        "challenge_id": inspected["challenge_id"],
        "selected_tile_indices": [0],
        "confirm": True,
        "plan_id": plan["plan_id"],
    })

    # The fake token from _make_mock_page is "fake-recaptcha-token-abc123".
    # It SHOULD appear in `out["token"]` (response field), but NEVER in
    # any evidence row inside plan_verification.
    assert "fake-recaptcha-token" in out.get("token", ""), (
        "sanity: token populated at top level"
    )

    pv = out["plan_verification"]
    serialized = json.dumps(pv)
    assert "fake-recaptcha-token" not in serialized, (
        f"raw token leaked into plan_verification: {serialized!r}"
    )
    # And the explicit field is the bool, not a string.
    record = pv["checklist"][0]["matched_evidence"][0]
    assert record["token_populated"] is True
    assert "token" not in record, (
        f"'token' key present in evidence record: {record}"
    )


# ---------------------------------------------------------------------------
# Continue / multi-round
# ---------------------------------------------------------------------------

def test_solve_continue_status_marks_token_populated_false(monkeypatch):
    """When dynamic-replace mode triggers (status='continue'), there's
    no token yet — token_populated must be False. CPs asserting
    'captcha solved' should not match a continue-mid-loop record."""
    vc = _reload_modules_with_env(
        monkeypatch, QA_VISUAL_CHALLENGE_CONSENT="true"
    )
    from tests.test_visual_challenge import _make_mock_page
    page = _make_mock_page(
        challenge_text=(
            "Select all images with buses\n"
            "Click verify once there are none left."
        ),
    )

    plan = qa_plan_tool({
        "task": "Solve dynamic captcha",
        "critical_points": [
            {
                "kind": "happy_path",
                "description": "captcha mid-loop",
                "verification_hint": "continue",
            },
        ],
    })

    inspected = vc.inspect_visual_challenge_tool({"_page": page})
    out = vc.solve_visual_challenge_tool({
        "challenge_id": inspected["challenge_id"],
        "selected_tile_indices": [0],
        "confirm": True,
        "plan_id": plan["plan_id"],
    })

    assert out["status"] == "continue"
    assert "plan_verification" in out
    record = out["plan_verification"]["checklist"][0]["matched_evidence"][0]
    assert record["status"] == "continue"
    assert record["token_populated"] is False, (
        "continue mid-loop has no token yet — bool MUST be False"
    )
    assert record["rounds_used"] >= 1


# ---------------------------------------------------------------------------
# §11 #3 — consent missing skips plan load entirely
# ---------------------------------------------------------------------------

def test_solve_with_consent_missing_skips_plan_verification(monkeypatch):
    """When consent is missing, solve returns `consent_required` BEFORE
    plan_id is even looked at — plan_verification must not appear. The
    plan should stay untouched (no spurious load / verify cycle)."""
    # NO QA_VISUAL_CHALLENGE_CONSENT env — consent gate fires.
    vc = _reload_modules_with_env(monkeypatch)
    out = vc.solve_visual_challenge_tool({
        "challenge_id": "anything",
        "selected_tile_indices": [0],
        "confirm": True,
        "plan_id": "any-plan-id",
    })

    assert out["error"] == "consent_required"
    assert "plan_verification" not in out


# ---------------------------------------------------------------------------
# Pre-solve early returns must not attach plan_verification
# ---------------------------------------------------------------------------

def test_solve_confirm_missing_skips_plan_verification(vc, page):
    """confirm_required fires before execute_solve — no plan_verification."""
    inspected = vc.inspect_visual_challenge_tool({"_page": page})
    out = vc.solve_visual_challenge_tool({
        "challenge_id": inspected["challenge_id"],
        "selected_tile_indices": [0],
        # confirm omitted
        "plan_id": "any-plan-id",
    })
    assert out["status"] == "confirm_required"
    assert "plan_verification" not in out


def test_solve_challenge_not_found_skips_plan_verification(vc):
    """challenge_not_found is a usage error — no plan_verification."""
    out = vc.solve_visual_challenge_tool({
        "challenge_id": "this-id-was-never-issued",
        "selected_tile_indices": [0],
        "confirm": True,
        "plan_id": "any-plan-id",
    })
    assert out["status"] == "challenge_not_found"
    assert "plan_verification" not in out


# ---------------------------------------------------------------------------
# verify_plan error envelope surfacing
# ---------------------------------------------------------------------------

def test_solve_with_unknown_plan_id_surfaces_plan_not_found(vc, page):
    """When plan_id points to a non-existent plan, verify_plan errors
    surface UNDER plan_verification — the solve itself completed fine."""
    inspected = vc.inspect_visual_challenge_tool({"_page": page})
    out = vc.solve_visual_challenge_tool({
        "challenge_id": inspected["challenge_id"],
        "selected_tile_indices": [0],
        "confirm": True,
        "plan_id": "deadbeef0000",
    })

    assert out["status"] == "passed"  # solve was fine
    assert "plan_verification" in out
    assert out["plan_verification"].get("error") == "plan_not_found"
