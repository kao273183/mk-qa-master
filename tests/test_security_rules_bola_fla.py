"""Unit tests for `security_rules.bola` — both BOLA (API1) and FLA (API5)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mk_qa_master.security_rules import (
    APIClient,
    AuthPair,
    OperationContext,
    Severity,
    bola_rule,
    function_authz_rule,
)
from mk_qa_master.security_rules.bola import (
    _count_path_params,
    _matches_admin_pattern,
    _substitute_first_path_param,
)


def _fake_response(*, status: int = 200, text: str = ""):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    return resp


def _op(path: str, *, method: str = "GET", requires_auth: bool = True) -> OperationContext:
    return OperationContext(
        method=method, path=path, operation_id=None,
        requires_auth=requires_auth, spec={},
    )


def _client(*, auth_pair: AuthPair | None = None, response=None,
            responses: list | None = None) -> APIClient:
    c = APIClient(base_url="http://test", auth_pair=auth_pair)
    if responses:
        c.request = MagicMock(side_effect=responses)
    else:
        c.request = MagicMock(return_value=response or _fake_response(status=200))
    return c


def _pair(**overrides) -> AuthPair:
    defaults = dict(
        user_a_token="alice-token",
        user_b_token="bob-token",
        bola_test_ids={"user_a": [1, 3], "user_b": [2]},
    )
    defaults.update(overrides)
    return AuthPair(**defaults)


# ---- helpers --------------------------------------------------------------

@pytest.mark.parametrize("path,expected", [
    ("/orders/{id}", 1),
    ("/users/{user_id}/orders/{order_id}", 2),
    ("/health", 0),
    ("/a/{b}/c/{d}/e/{f}", 3),
])
def test_count_path_params(path, expected):
    assert _count_path_params(path) == expected


def test_substitute_first_path_param():
    assert _substitute_first_path_param("/orders/{id}", 5) == "/orders/5"
    assert _substitute_first_path_param("/no/params", 5) == "/no/params"
    # Multi-param: only the first substituted
    assert _substitute_first_path_param("/users/{u}/orders/{o}", 9) == "/users/9/orders/{o}"


def test_matches_admin_pattern():
    assert _matches_admin_pattern("/admin/users", ["/admin/"]) is True
    assert _matches_admin_pattern("/safe/admin/users", ["/admin/"]) is True
    assert _matches_admin_pattern("/users", ["/admin/"]) is False
    assert _matches_admin_pattern("/internal/reports", ["/admin/", "/internal/"]) is True


# ---- BOLA: applies_to ----------------------------------------------------

def test_bola_applies_to_single_path_param_get():
    assert bola_rule.applies_to(_op("/orders/{id}")) is True


def test_bola_skips_no_auth_op():
    assert bola_rule.applies_to(_op("/orders/{id}", requires_auth=False)) is False


def test_bola_skips_post():
    assert bola_rule.applies_to(_op("/orders/{id}", method="POST")) is False


def test_bola_skips_no_path_param():
    """No path param → nothing to substitute → can't BOLA-test."""
    assert bola_rule.applies_to(_op("/orders")) is False


def test_bola_skips_multi_path_param_for_now():
    """Filed for v0.8.1. Multi-param substitution needs richer
    strategy than 'replace the only one with target id'."""
    assert bola_rule.applies_to(_op("/users/{u}/orders/{o}")) is False


# ---- BOLA: skip-with-info conditions -------------------------------------

def test_bola_skips_with_info_when_no_auth_pair():
    c = _client(auth_pair=None)
    findings = bola_rule.execute(c, _op("/orders/{id}"))
    assert len(findings) == 1
    assert findings[0].rule_id.endswith("-Skipped")
    assert findings[0].severity == Severity.INFO
    assert findings[0].evidence["reason"] == "no_auth_pair_provided"
    c.request.assert_not_called()


def test_bola_skips_with_info_when_no_bola_ids():
    c = _client(auth_pair=AuthPair("a", "b", bola_test_ids=None))
    findings = bola_rule.execute(c, _op("/orders/{id}"))
    assert findings[0].evidence["reason"] == "no_bola_test_ids"


def test_bola_skips_with_info_when_one_side_empty():
    c = _client(auth_pair=AuthPair("a", "b",
                                   bola_test_ids={"user_a": [1], "user_b": []}))
    findings = bola_rule.execute(c, _op("/orders/{id}"))
    assert findings[0].evidence["reason"] == "incomplete_bola_test_ids"


# ---- BOLA: core behavior --------------------------------------------------

def test_bola_two_directions_both_2xx_yields_two_critical_findings():
    """Both directions of the diff return 200 → 2 CRITICAL findings."""
    c = _client(
        auth_pair=_pair(),
        responses=[
            _fake_response(status=200, text='{"id":2,"owner_id":2,"item":"bob pizza"}'),
            _fake_response(status=200, text='{"id":1,"owner_id":1,"item":"alice coffee"}'),
        ],
    )
    findings = bola_rule.execute(c, _op("/orders/{id}"))
    crit = [f for f in findings if f.severity == Severity.CRITICAL]
    assert len(crit) == 2
    assert {f.evidence["actor"] for f in crit} == {"user_a", "user_b"}
    assert {f.evidence["target_owner"] for f in crit} == {"user_a", "user_b"}


def test_bola_safe_endpoint_403_yields_no_findings():
    c = _client(
        auth_pair=_pair(),
        responses=[_fake_response(status=403), _fake_response(status=403)],
    )
    findings = bola_rule.execute(c, _op("/orders/{id}"))
    assert findings == []


def test_bola_404_treated_as_safe():
    """404 = id doesn't exist for this user → no leak."""
    c = _client(
        auth_pair=_pair(),
        responses=[_fake_response(status=404), _fake_response(status=404)],
    )
    findings = bola_rule.execute(c, _op("/orders/{id}"))
    assert findings == []


def test_bola_one_direction_succeeds_other_fails():
    """Asymmetric vuln: alice can read bob's order but not vice versa."""
    c = _client(
        auth_pair=_pair(),
        responses=[
            _fake_response(status=200, text='{"item":"bob"}'),  # alice → bob's id: 200
            _fake_response(status=403),                          # bob → alice's id: 403
        ],
    )
    findings = bola_rule.execute(c, _op("/orders/{id}"))
    crit = [f for f in findings if f.severity == Severity.CRITICAL]
    assert len(crit) == 1
    assert crit[0].evidence["actor"] == "user_a"
    assert crit[0].evidence["target_owner"] == "user_b"


def test_bola_substitutes_correct_id_into_path():
    """The probed path must contain user-B's id, not the template `{id}`."""
    c = _client(auth_pair=_pair(),
                responses=[_fake_response(status=403), _fake_response(status=403)])
    bola_rule.execute(c, _op("/orders/{id}"))
    paths_called = [call.args[1] for call in c.request.call_args_list]
    assert paths_called[0] == "/orders/2"  # alice → bob's id
    assert paths_called[1] == "/orders/1"  # bob → alice's id


def test_bola_finding_carries_remediation_hint():
    c = _client(auth_pair=_pair(),
                responses=[_fake_response(status=200, text='{}'),
                           _fake_response(status=403)])
    findings = bola_rule.execute(c, _op("/orders/{id}"))
    crit = next(f for f in findings if f.severity == Severity.CRITICAL)
    assert "object-level authorization" in crit.title
    assert "owner" in crit.remediation_hint
    assert crit.evidence["status_code"] == 200


# ---- FLA: applies_to -----------------------------------------------------

def test_fla_applies_to_admin_path():
    assert function_authz_rule.applies_to(_op("/admin/users")) is True


def test_fla_applies_to_safe_admin_path_too():
    """We can't tell from `applies_to` whether this is the vuln vs
    safe variant — the rule probes and lets the response tell us."""
    assert function_authz_rule.applies_to(_op("/safe/admin/users")) is True


def test_fla_skips_no_auth_op():
    assert function_authz_rule.applies_to(_op("/admin/users", requires_auth=False)) is False


# ---- FLA: no_auth_pair surfacing ----------------------------------------

def test_fla_no_pair_emits_info_on_admin_path_only():
    c = _client(auth_pair=None)
    findings = function_authz_rule.execute(c, _op("/admin/users"))
    assert len(findings) == 1
    assert findings[0].severity == Severity.INFO


def test_fla_no_pair_silent_on_non_admin_path():
    """Don't drown the output in 'skipped' findings on every endpoint."""
    c = _client(auth_pair=None)
    findings = function_authz_rule.execute(c, _op("/users"))
    assert findings == []


# ---- FLA: core behavior ---------------------------------------------------

def test_fla_low_priv_token_gets_2xx_yields_high_finding():
    c = _client(auth_pair=_pair(),
                response=_fake_response(status=200, text='{"users":[...]}'))
    findings = function_authz_rule.execute(c, _op("/admin/users"))
    high = [f for f in findings if f.severity == Severity.HIGH]
    assert len(high) == 1
    assert high[0].rule_id.endswith("NonAdminAccessGranted")
    assert high[0].evidence["low_priv_user"] == "user_a"


def test_fla_low_priv_token_gets_403_no_finding():
    c = _client(auth_pair=_pair(),
                response=_fake_response(status=403))
    findings = function_authz_rule.execute(c, _op("/admin/users"))
    assert findings == []


def test_fla_uses_user_a_token_by_default():
    """The default low-priv user is `user_a`."""
    c = _client(auth_pair=_pair(),
                response=_fake_response(status=403))
    function_authz_rule.execute(c, _op("/admin/users"))
    sent_token = c.request.call_args.kwargs["token"]
    assert sent_token == "alice-token"


def test_fla_honors_fla_low_priv_user_override():
    """If user specifies user_b as low-priv, that token is used."""
    pair = _pair(fla_low_priv_user="user_b")
    c = _client(auth_pair=pair, response=_fake_response(status=403))
    function_authz_rule.execute(c, _op("/admin/users"))
    sent_token = c.request.call_args.kwargs["token"]
    assert sent_token == "bob-token"


def test_fla_honors_custom_admin_paths():
    """Custom `fla_admin_paths` lets users mark non-`/admin/` paths
    as elevated."""
    pair = _pair(fla_admin_paths=["/internal/"])
    c = _client(auth_pair=pair, response=_fake_response(status=200))
    # /admin/users no longer matches
    findings_admin = function_authz_rule.execute(c, _op("/admin/users"))
    # /internal/reports DOES match
    findings_internal = function_authz_rule.execute(c, _op("/internal/reports"))
    assert findings_admin == []
    assert len(findings_internal) == 1


def test_fla_non_admin_path_no_findings_even_with_pair():
    c = _client(auth_pair=_pair(),
                response=_fake_response(status=200))
    findings = function_authz_rule.execute(c, _op("/users"))
    assert findings == []
    c.request.assert_not_called()


def test_fla_probe_exception_yields_info():
    import requests
    c = _client(auth_pair=_pair())
    c.request = MagicMock(side_effect=requests.ConnectionError("boom"))
    findings = function_authz_rule.execute(c, _op("/admin/users"))
    assert len(findings) == 1
    assert findings[0].severity == Severity.INFO
