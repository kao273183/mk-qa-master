"""Unit tests for `security_rules.headers_misconfig`.

These tests mock the `requests` layer to assert finding shape and
orchestration correctness. The real-HTTP "does this rule actually
catch the vuln" assertion lives in
`examples/sample_vulnerable_api/tests/test_rule_headers_dogfood.py`
per PRD §10 (mock tests catch orchestrator regressions; dogfood
tests catch "the rule's actually broken").
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from mk_qa_master.security_rules import (
    APIClient,
    Finding,
    HeadersMisconfigRule,
    OperationContext,
    Severity,
)
from mk_qa_master.security_rules.headers_misconfig import (
    REQUIRED_HEADERS,
    _resolve_path,
    rule,
)


# ---- helpers --------------------------------------------------------------

def _fake_response(*, status: int = 200, headers: dict[str, str] | None = None):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = dict(headers or {})
    return resp


def _op(path: str = "/some", method: str = "GET", requires_auth: bool = False) -> OperationContext:
    return OperationContext(
        method=method,
        path=path,
        operation_id=None,
        requires_auth=requires_auth,
        spec={},
    )


def _client_with_response(resp) -> APIClient:
    client = APIClient(base_url="http://test")
    client.request = MagicMock(return_value=resp)
    return client


# ---- applies_to ----------------------------------------------------------

def test_applies_to_only_GET():
    assert rule.applies_to(_op(method="GET")) is True
    assert rule.applies_to(_op(method="POST")) is False
    assert rule.applies_to(_op(method="PUT")) is False
    assert rule.applies_to(_op(method="DELETE")) is False


# ---- _resolve_path -------------------------------------------------------

@pytest.mark.parametrize(
    "input_path,expected",
    [
        ("/orders/{id}", "/orders/1"),
        ("/users/{user_id}/orders", "/users/1/orders"),
        ("/health", "/health"),
        ("/a/{b}/c/{d}/e", "/a/1/c/1/e"),
        # Malformed: unterminated `{` falls through unchanged from the
        # opening brace onward; treat as best-effort.
        ("/foo/{bar", "/foo/{bar"),
    ],
)
def test_resolve_path(input_path, expected):
    assert _resolve_path(input_path) == expected


# ---- core behavior --------------------------------------------------------

def test_all_required_headers_missing_yields_4_medium_findings():
    """A response with zero security headers should produce one
    MissingHeader finding per item in REQUIRED_HEADERS — no more,
    no less.
    """
    client = _client_with_response(_fake_response(headers={}))
    findings = rule.execute(client, _op("/no-headers"))
    missing = [f for f in findings if f.rule_id.endswith("-MissingHeader")]
    assert len(missing) == 4
    assert {f.evidence["missing_header"] for f in missing} == set(REQUIRED_HEADERS)
    assert all(f.severity == Severity.MEDIUM for f in missing)


def test_all_required_headers_present_yields_no_missing_findings():
    """When every required header is set, no MissingHeader finding."""
    client = _client_with_response(_fake_response(headers={
        "Strict-Transport-Security": "max-age=31536000",
        "Content-Security-Policy": "default-src 'self'",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
    }))
    findings = rule.execute(client, _op("/all-headers"))
    assert not any(f.rule_id.endswith("-MissingHeader") for f in findings)


def test_header_check_is_case_insensitive():
    """HTTP header names are case-insensitive; lowercase variants count."""
    client = _client_with_response(_fake_response(headers={
        "strict-transport-security": "max-age=31536000",
        "content-security-policy": "default-src 'self'",
        "x-content-type-options": "nosniff",
        "x-frame-options": "DENY",
    }))
    findings = rule.execute(client, _op("/lowercase"))
    assert not any(f.rule_id.endswith("-MissingHeader") for f in findings)


def test_wildcard_cors_with_credentials_is_HIGH():
    client = _client_with_response(_fake_response(headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Credentials": "true",
    }))
    findings = rule.execute(client, _op("/cors-dangerous"))
    cors = [f for f in findings if "CORS" in f.rule_id]
    assert len(cors) == 1
    assert cors[0].severity == Severity.HIGH
    assert cors[0].rule_id.endswith("CORSWildcardWithCredentials")


def test_wildcard_cors_alone_is_LOW():
    client = _client_with_response(_fake_response(headers={
        "Access-Control-Allow-Origin": "*",
        # No allow-credentials
    }))
    findings = rule.execute(client, _op("/cors-wildcard"))
    cors = [f for f in findings if "CORS" in f.rule_id]
    assert len(cors) == 1
    assert cors[0].severity == Severity.LOW
    assert cors[0].rule_id.endswith("CORSWildcardOrigin")


def test_specific_cors_origin_no_cors_finding():
    """Origin restricted to a real domain should not fire any CORS finding."""
    client = _client_with_response(_fake_response(headers={
        "Access-Control-Allow-Origin": "https://app.example.com",
        "Access-Control-Allow-Credentials": "true",
    }))
    findings = rule.execute(client, _op("/cors-specific"))
    assert not any("CORS" in f.rule_id for f in findings)


def test_credentials_lowercase_true_still_triggers():
    """`Access-Control-Allow-Credentials` is allowed to be `True`/`true`/`TRUE`."""
    client = _client_with_response(_fake_response(headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Credentials": "True",
    }))
    findings = rule.execute(client, _op("/cors-mixed-case"))
    high = [f for f in findings if f.severity == Severity.HIGH]
    assert len(high) == 1


# ---- skipped + error paths ------------------------------------------------

def test_skipped_when_auth_required_but_no_token():
    """If the op requires auth and the client has no default token,
    the rule emits an INFO Skipped finding rather than scanning the
    401 response (which would falsely look "header-poor")."""
    client = APIClient(base_url="http://test", default_token=None)
    client.request = MagicMock()  # would-be probe
    findings = rule.execute(client, _op("/needs-auth", requires_auth=True))
    assert len(findings) == 1
    assert findings[0].rule_id.endswith("-Skipped")
    assert findings[0].severity == Severity.INFO
    client.request.assert_not_called()


def test_request_exception_yields_info_finding():
    import requests
    client = APIClient(base_url="http://test")
    client.request = MagicMock(side_effect=requests.ConnectionError("boom"))
    findings = rule.execute(client, _op("/unreachable"))
    assert len(findings) == 1
    assert findings[0].rule_id.endswith("-ProbeFailed")
    assert findings[0].severity == Severity.INFO
    assert "ConnectionError" in findings[0].evidence["error"]


# ---- Finding serialization ----------------------------------------------

def test_finding_to_dict_roundtrip():
    f = Finding(
        rule_id="OWASP-API8-Headers-MissingHeader",
        severity=Severity.MEDIUM,
        endpoint="GET /x",
        title="missing CSP",
        evidence={"missing_header": "Content-Security-Policy"},
        remediation_hint="set it",
    )
    d = f.to_dict()
    assert d["severity"] == "medium"  # serialized as string, not enum repr
    assert d["evidence"]["missing_header"] == "Content-Security-Policy"


# ---- Severity ordering / threshold filtering -----------------------------

@pytest.mark.parametrize(
    "finding_sev,threshold,expected",
    [
        (Severity.CRITICAL, Severity.MEDIUM, True),
        (Severity.HIGH,     Severity.MEDIUM, True),
        (Severity.MEDIUM,   Severity.MEDIUM, True),
        (Severity.LOW,      Severity.MEDIUM, False),
        (Severity.INFO,     Severity.MEDIUM, False),
        (Severity.INFO,     Severity.INFO,   True),
    ],
)
def test_severity_meets_threshold(finding_sev, threshold, expected):
    assert finding_sev.meets(threshold) is expected
