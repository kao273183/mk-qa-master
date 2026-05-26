"""Unit tests for `security_rules.broken_auth` (OWASP API2).

Mocks the requests layer to assert orchestration. Real-HTTP
positive/negative against the Tier 1 fixture lives in
`examples/sample_vulnerable_api/tests/test_rule_broken_auth_dogfood.py`.
"""
from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock

import jwt as pyjwt
import pytest

from mk_qa_master.security_rules import (
    APIClient,
    BrokenAuthRule,
    OperationContext,
    Severity,
)
from mk_qa_master.security_rules.broken_auth import (
    _ATTACKER_SECRET,
    _encode_wrong_sig_jwt,
    _forge_alg_none_jwt,
    rule,
)


# ---- helpers --------------------------------------------------------------

def _fake_response(*, status: int = 200, text: str = ""):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    return resp


def _op(path: str = "/users/me", method: str = "GET",
        requires_auth: bool = True) -> OperationContext:
    return OperationContext(
        method=method,
        path=path,
        operation_id=None,
        requires_auth=requires_auth,
        spec={},
    )


def _client_returning(*responses):
    """Build a client that returns the given responses in order."""
    client = APIClient(base_url="http://test")
    client.request = MagicMock(side_effect=list(responses))
    return client


# ---- applies_to -----------------------------------------------------------

def test_applies_only_to_auth_required_endpoints():
    assert rule.applies_to(_op(requires_auth=True)) is True
    assert rule.applies_to(_op(requires_auth=False)) is False


# ---- forge helpers --------------------------------------------------------

def test_forge_alg_none_has_three_dot_separated_segments():
    token = _forge_alg_none_jwt({"sub": "x"})
    parts = token.split(".")
    assert len(parts) == 3
    assert parts[2] == "", "alg:none signature segment must be empty"


def test_forge_alg_none_header_says_none():
    token = _forge_alg_none_jwt({"sub": "x"})
    header_b64 = token.split(".")[0]
    # Re-pad and decode
    pad = "=" * (-len(header_b64) % 4)
    header = json.loads(base64.urlsafe_b64decode(header_b64 + pad))
    assert header["alg"] == "none"


def test_wrong_sig_jwt_signed_with_attacker_key():
    """The wrong-sig token must decode against `_ATTACKER_SECRET`
    (proving it's signed) but NOT against any other secret used by
    the server under test."""
    token = _encode_wrong_sig_jwt({"sub": "x", "role": "admin"})
    decoded = pyjwt.decode(token, _ATTACKER_SECRET, algorithms=["HS256"])
    assert decoded["role"] == "admin"

    # The verification key must be ≥ 32 bytes for HS256 per RFC 7518 §3.2;
    # PyJWT warns otherwise. The key value itself doesn't matter — what we
    # need is "any key that isn't `_ATTACKER_SECRET`."
    with pytest.raises(pyjwt.InvalidSignatureError):
        pyjwt.decode(token, "some-other-server-secret-padded-to-32-plus-bytes-yes",
                     algorithms=["HS256"])


# ---- full probe matrix on a fully-vulnerable mock --------------------------

def test_all_2xx_yields_four_findings_with_expected_severities():
    """When the mock server accepts every forged token, the rule must
    emit one finding per probe, severities matching the probe matrix.
    """
    client = _client_returning(
        _fake_response(status=200, text='{"role":"admin"}'),  # NoAuth
        _fake_response(status=200, text='{"role":"admin"}'),  # MalformedJWT
        _fake_response(status=200, text='{"role":"admin"}'),  # AlgNone
        _fake_response(status=200, text='{"role":"admin"}'),  # WrongSignature
    )
    findings = rule.execute(client, _op())
    by_probe = {f.evidence["probe"]: f for f in findings if "probe" in f.evidence}
    assert set(by_probe) == {"NoAuth", "MalformedJWT", "AlgNone", "WrongSignature"}
    assert by_probe["NoAuth"].severity == Severity.CRITICAL
    assert by_probe["MalformedJWT"].severity == Severity.MEDIUM
    assert by_probe["AlgNone"].severity == Severity.HIGH
    assert by_probe["WrongSignature"].severity == Severity.HIGH


# ---- all probes rejected → no findings ------------------------------------

def test_all_401_yields_no_findings():
    client = _client_returning(
        _fake_response(status=401),  # NoAuth
        _fake_response(status=401),  # MalformedJWT
        _fake_response(status=401),  # AlgNone
        _fake_response(status=401),  # WrongSignature
    )
    findings = rule.execute(client, _op())
    assert findings == []


def test_403_also_counts_as_safe():
    client = _client_returning(
        _fake_response(status=403),
        _fake_response(status=403),
        _fake_response(status=403),
        _fake_response(status=403),
    )
    findings = rule.execute(client, _op())
    assert findings == []


def test_non_auth_4xx_treated_as_safe():
    """A 400 / 422 means the server rejected the token but not for
    auth reasons — still safe for THIS rule's purposes (the server
    didn't honor the forged credentials)."""
    client = _client_returning(
        _fake_response(status=400),
        _fake_response(status=422),
        _fake_response(status=400),
        _fake_response(status=400),
    )
    findings = rule.execute(client, _op())
    assert findings == []


# ---- partial vulnerability — selective 2xx ---------------------------------

def test_only_alg_none_succeeds_only_alg_none_flagged():
    """A server that pins signature checks but doesn't pin alg:
    only the AlgNone probe should fire."""
    client = _client_returning(
        _fake_response(status=401),  # NoAuth
        _fake_response(status=401),  # MalformedJWT
        _fake_response(status=200),  # AlgNone   ← only this
        _fake_response(status=401),  # WrongSignature
    )
    findings = rule.execute(client, _op())
    assert len(findings) == 1
    assert findings[0].evidence["probe"] == "AlgNone"
    assert findings[0].severity == Severity.HIGH


def test_only_no_auth_succeeds_no_auth_flagged_critical():
    client = _client_returning(
        _fake_response(status=200),  # NoAuth — server has no auth check
        _fake_response(status=401),
        _fake_response(status=401),
        _fake_response(status=401),
    )
    findings = rule.execute(client, _op())
    assert len(findings) == 1
    assert findings[0].evidence["probe"] == "NoAuth"
    assert findings[0].severity == Severity.CRITICAL


# ---- request-level errors -------------------------------------------------

def test_probe_failure_yields_info_finding_continues_other_probes():
    import requests
    client = APIClient(base_url="http://test")
    # First probe raises, rest succeed (mock 200)
    client.request = MagicMock(side_effect=[
        requests.ConnectionError("boom"),
        _fake_response(status=200),
        _fake_response(status=200),
        _fake_response(status=200),
    ])
    findings = rule.execute(client, _op())
    # 1 INFO (probe failure) + 3 findings for the others
    info = [f for f in findings if f.severity == Severity.INFO]
    flagged = [f for f in findings if f.severity != Severity.INFO]
    assert len(info) == 1
    assert "NoAuth" in info[0].rule_id
    assert len(flagged) == 3
    assert {f.evidence["probe"] for f in flagged} == {
        "MalformedJWT", "AlgNone", "WrongSignature",
    }


# ---- request shape — tokens are actually sent with the right Authorization

def test_no_auth_probe_sends_token_none():
    """The NoAuth probe must explicitly call client.request(token=None)
    so the client omits the Authorization header — not pass the
    default token by mistake."""
    client = APIClient(base_url="http://test", default_token="real-token")
    client.request = MagicMock(return_value=_fake_response(status=401))
    rule.execute(client, _op())
    calls = client.request.call_args_list
    # First call is NoAuth — token kwarg should be None
    assert calls[0].kwargs["token"] is None


def test_other_probes_send_forged_tokens_not_default():
    client = APIClient(base_url="http://test", default_token="real-token")
    client.request = MagicMock(return_value=_fake_response(status=401))
    rule.execute(client, _op())
    calls = client.request.call_args_list
    # Calls 1-3 send forged tokens; none of them is the real default.
    for c in calls[1:]:
        sent_token = c.kwargs["token"]
        assert sent_token is not None
        assert sent_token != "real-token"


# ---- finding evidence shape ----------------------------------------------

def test_finding_evidence_includes_probe_status_and_body_preview():
    client = _client_returning(
        _fake_response(status=200, text='{"role":"admin","sub":999}'),
        _fake_response(status=401),
        _fake_response(status=401),
        _fake_response(status=401),
    )
    findings = rule.execute(client, _op("/admin/dashboard"))
    assert len(findings) == 1
    f = findings[0]
    assert f.evidence["probe"] == "NoAuth"
    assert f.evidence["status_code"] == 200
    assert "role" in f.evidence["response_body_preview"]
    assert f.endpoint == "GET /admin/dashboard"
