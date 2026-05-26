"""Unit tests for `security_rules.mass_assignment` (OWASP API3)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mk_qa_master.security_rules import (
    APIClient,
    MassAssignmentRule,
    OperationContext,
    Severity,
    mass_assignment_rule as rule,
)
from mk_qa_master.security_rules.mass_assignment import (
    DEFAULT_DANGEROUS_FIELDS,
    _classify,
    _extract_json_schema,
    _minimal_body_from_schema,
    _placeholder_for,
)


# ---- helpers --------------------------------------------------------------

def _fake_response(*, status: int = 200, json_body=None, text: str = ""):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text or (str(json_body) if json_body else "")
    if json_body is not None:
        resp.json = MagicMock(return_value=json_body)
    else:
        resp.json = MagicMock(side_effect=ValueError("not JSON"))
    return resp


def _op(method: str = "POST", path: str = "/signup",
        schema: dict | None = None) -> OperationContext:
    spec = {}
    if schema is not None:
        spec = {
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": schema,
                    }
                }
            }
        }
    return OperationContext(
        method=method, path=path, operation_id=None,
        requires_auth=False, spec=spec,
    )


def _signup_schema() -> dict:
    return {
        "type": "object",
        "required": ["username", "password"],
        "additionalProperties": False,
        "properties": {
            "username": {"type": "string"},
            "password": {"type": "string"},
        },
    }


# ---- helper functions ----------------------------------------------------

def test_minimal_body_only_includes_required_fields():
    schema = {
        "type": "object",
        "required": ["username", "password"],
        "properties": {
            "username": {"type": "string"},
            "password": {"type": "string"},
            "nickname": {"type": "string"},  # not required → skipped
        },
    }
    body = _minimal_body_from_schema(schema)
    assert set(body) == {"username", "password"}


def test_placeholder_picks_email_for_email_fields():
    p = _placeholder_for({"type": "string", "format": "email"}, "email")
    assert "@" in p


def test_placeholder_uses_enum_first_value_when_present():
    p = _placeholder_for(
        {"type": "string", "enum": ["alice", "bob", "charlie"]}, "name"
    )
    assert p == "alice"


@pytest.mark.parametrize("type_,expected", [
    ("string", "scan-test"),
    ("integer", 1),
    ("number", 1.0),
    ("boolean", False),
    ("array", []),
])
def test_placeholder_by_type(type_, expected):
    assert _placeholder_for({"type": type_}, "x") == expected


def test_extract_schema_returns_none_when_missing():
    op = _op(schema=None)
    assert _extract_json_schema(op) is None


def test_extract_schema_returns_dict_when_present():
    op = _op(schema=_signup_schema())
    assert _extract_json_schema(op) == _signup_schema()


# ---- _classify ------------------------------------------------------------

def test_classify_vulnerable_when_field_echoed_at_same_value():
    resp = _fake_response(status=201, json_body={"role": "admin", "username": "x"})
    outcome = _classify("role", "admin", resp)
    assert outcome.classification == "vulnerable"


def test_classify_safe_overridden_when_field_echoed_at_different_value():
    resp = _fake_response(status=201, json_body={"role": "user", "username": "x"})
    outcome = _classify("role", "admin", resp)
    assert outcome.classification == "safe_overridden"


def test_classify_inconclusive_when_field_absent_from_2xx_response():
    resp = _fake_response(status=201, json_body={"username": "x"})
    outcome = _classify("role", "admin", resp)
    assert outcome.classification == "inconclusive"


def test_classify_safe_rejected_on_4xx():
    resp = _fake_response(status=400)
    outcome = _classify("role", "admin", resp)
    assert outcome.classification == "safe_rejected"


def test_classify_inconclusive_when_response_not_json():
    resp = _fake_response(status=201, text="ok")
    outcome = _classify("role", "admin", resp)
    assert outcome.classification == "inconclusive"


# ---- applies_to -----------------------------------------------------------

def test_applies_to_post_with_json_schema():
    assert rule.applies_to(_op("POST", "/signup", _signup_schema())) is True


def test_applies_to_put_with_json_schema():
    assert rule.applies_to(_op("PUT", "/users/1", _signup_schema())) is True


def test_applies_to_patch_with_json_schema():
    assert rule.applies_to(_op("PATCH", "/users/1", _signup_schema())) is True


def test_does_not_apply_to_get():
    assert rule.applies_to(_op("GET", "/users", _signup_schema())) is False


def test_does_not_apply_to_delete():
    assert rule.applies_to(_op("DELETE", "/users/1", _signup_schema())) is False


def test_does_not_apply_to_post_without_json_body_schema():
    assert rule.applies_to(_op("POST", "/upload")) is False


# ---- core behavior -------------------------------------------------------

def test_vulnerable_endpoint_yields_high_finding_per_echoed_field():
    """A server that echoes every dangerous field at the sent value:
    one HIGH finding per probe (10 default dangerous fields → 10 HIGH).
    """
    # Echo back EVERYTHING we send — simulates the textbook vulnerable
    # endpoint that does `record = dict(body)`.
    def echo_handler(method, path, *, json_body, **kw):
        return _fake_response(status=201, json_body=json_body)

    c = APIClient(base_url="http://test")
    c.request = MagicMock(side_effect=echo_handler)
    findings = rule.execute(c, _op("POST", "/vuln", _signup_schema()))
    high = [f for f in findings if f.severity == Severity.HIGH]
    # Default catalog has 10 fields; none of them are in the schema's
    # whitelisted properties, so all 10 should fire.
    assert len(high) == len(DEFAULT_DANGEROUS_FIELDS)
    # Each finding identifies the field correctly
    fields_flagged = {f.evidence["field"] for f in high}
    assert fields_flagged == {name for name, _ in DEFAULT_DANGEROUS_FIELDS}


def test_safe_endpoint_returning_400_yields_no_findings():
    c = APIClient(base_url="http://test")
    c.request = MagicMock(return_value=_fake_response(status=400))
    findings = rule.execute(c, _op("POST", "/safe", _signup_schema()))
    serious = [f for f in findings if f.severity != Severity.INFO]
    assert serious == []


def test_safe_overridden_endpoint_no_findings():
    """Server accepts the request but forces role=user regardless."""
    def override_handler(method, path, *, json_body, **kw):
        return _fake_response(status=201, json_body={
            **{k: v for k, v in json_body.items() if k in ("username", "password")},
            "role": "user",        # forced
            "is_admin": False,     # forced
            "isAdmin": False,
            "admin": False,
            "is_verified": False,
            "isVerified": False,
            "verified": False,
            "email_verified": False,
            # Force False — sent True, server overrides. (`is_active: True`
            # echoed at the sent value would correctly classify as vulnerable.)
            "is_active": False,
            "permissions": [],     # forced empty
        })

    c = APIClient(base_url="http://test")
    c.request = MagicMock(side_effect=override_handler)
    findings = rule.execute(c, _op("POST", "/safe", _signup_schema()))
    high = [f for f in findings if f.severity == Severity.HIGH]
    assert high == []


def test_inconclusive_endpoint_emits_info_findings():
    """Server 2xx-but-doesn't-echo: emit INFO per field, no HIGH."""
    c = APIClient(base_url="http://test")
    c.request = MagicMock(return_value=_fake_response(
        status=201, json_body={"id": 5, "username": "x"}
    ))
    findings = rule.execute(c, _op("POST", "/maybe", _signup_schema()))
    high = [f for f in findings if f.severity == Severity.HIGH]
    info = [f for f in findings if f.severity == Severity.INFO]
    assert high == []
    assert len(info) == len(DEFAULT_DANGEROUS_FIELDS)
    assert all("Inconclusive" in f.rule_id for f in info)


def test_dangerous_field_in_schema_properties_is_skipped():
    """If the spec explicitly declares `role` as a writable field
    (e.g. an admin-management endpoint), we DON'T flag it — it's
    legitimately part of the API contract, not over-posting."""
    schema = {
        "type": "object",
        "required": ["username", "password"],
        "properties": {
            "username": {"type": "string"},
            "password": {"type": "string"},
            "role": {"type": "string", "enum": ["user", "admin"]},  # declared!
        },
    }

    def echo_handler(method, path, *, json_body, **kw):
        return _fake_response(status=201, json_body=json_body)

    c = APIClient(base_url="http://test")
    c.request = MagicMock(side_effect=echo_handler)
    findings = rule.execute(c, _op("POST", "/admin-create", schema))
    # `role` should be skipped; other dangerous fields still probed.
    fields = {f.evidence["field"] for f in findings if f.severity == Severity.HIGH}
    assert "role" not in fields
    # The other 9 default fields are still in the catalog
    assert len(fields) == len(DEFAULT_DANGEROUS_FIELDS) - 1


def test_request_body_contains_required_fields_plus_one_dangerous():
    """Each probe's body contains both the spec's required fields and
    exactly one dangerous extra."""
    bodies_sent: list[dict] = []

    def capturing_handler(method, path, *, json_body, **kw):
        bodies_sent.append(json_body)
        return _fake_response(status=400)

    c = APIClient(base_url="http://test")
    c.request = MagicMock(side_effect=capturing_handler)
    rule.execute(c, _op("POST", "/x", _signup_schema()))

    assert len(bodies_sent) == len(DEFAULT_DANGEROUS_FIELDS)
    for body, (field_name, dangerous_val) in zip(bodies_sent, DEFAULT_DANGEROUS_FIELDS):
        assert body["username"] == "scan-test"
        assert body["password"] == "scan-test"
        assert body[field_name] == dangerous_val


def test_probe_exception_yields_info_finding_continues_other_fields():
    import requests
    c = APIClient(base_url="http://test")
    # First probe raises, rest return 400 (safe)
    side_effects = [requests.ConnectionError("boom")] + [
        _fake_response(status=400) for _ in range(len(DEFAULT_DANGEROUS_FIELDS) - 1)
    ]
    c.request = MagicMock(side_effect=side_effects)
    findings = rule.execute(c, _op("POST", "/x", _signup_schema()))
    info = [f for f in findings if f.severity == Severity.INFO
            and "ProbeFailed" in f.rule_id]
    assert len(info) == 1
    assert info[0].evidence["field"] == DEFAULT_DANGEROUS_FIELDS[0][0]


def test_custom_dangerous_fields_override():
    """Callers can swap in a custom catalog via attribute mutation."""
    custom_rule = MassAssignmentRule()
    custom_rule.dangerous_fields = [("custom_field", "boom")]

    def echo_handler(method, path, *, json_body, **kw):
        return _fake_response(status=201, json_body=json_body)

    c = APIClient(base_url="http://test")
    c.request = MagicMock(side_effect=echo_handler)
    findings = custom_rule.execute(c, _op("POST", "/x", _signup_schema()))
    high = [f for f in findings if f.severity == Severity.HIGH]
    assert len(high) == 1
    assert high[0].evidence["field"] == "custom_field"
