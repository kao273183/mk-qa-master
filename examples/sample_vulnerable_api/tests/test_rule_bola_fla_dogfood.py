"""Dogfood test — runs BOLA + FLA rules against the Tier 1 fixture.

Per PRD §10. Asserts:
  - BOLA flags `/vuln/orders/{id}` for cross-user data exposure
    (alice CAN read bob's order 2)
  - BOLA does NOT flag `/safe/me/orders` (the safe endpoint has no
    {id} param so it's filtered by applies_to anyway, but we cover
    the case explicitly)
  - FLA flags `/vuln/admin/users` (alice the non-admin gets 200)
  - FLA does NOT flag `/safe/admin/users` (non-admin gets 403)

The fixture's pre-seeded state from PR-1 maps:
  - alice → user_id=1 → owns orders 1, 3
  - bob   → user_id=2 → owns order 2
  - admin → user_id=99 → role=admin
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

from mk_qa_master.security_rules import (
    APIClient,
    AuthPair,
    OperationContext,
    Severity,
    bola_rule,
    function_authz_rule,
)

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
PORT = 5099
BASE_URL = f"http://127.0.0.1:{PORT}"


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        return s.connect_ex(("127.0.0.1", port)) == 0


@pytest.fixture(scope="module")
def vuln_app():
    if _port_open(PORT):
        pytest.skip(f"Port {PORT} already in use")
    proc = subprocess.Popen(
        [sys.executable, str(APP_PATH)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    try:
        deadline = time.time() + 5.0
        while time.time() < deadline:
            try:
                if requests.get(f"{BASE_URL}/health", timeout=0.5).status_code == 200:
                    break
            except requests.RequestException:
                pass
            time.sleep(0.1)
        else:
            stdout, stderr = proc.communicate(timeout=1)
            raise RuntimeError(f"Flask app didn't boot: {stderr.decode()!r}")
        yield BASE_URL
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


def _login(username: str, password: str) -> str:
    r = requests.post(f"{BASE_URL}/login",
                      json={"username": username, "password": password},
                      timeout=2)
    r.raise_for_status()
    return r.json()["token"]


@pytest.fixture(scope="module")
def auth_pair_alice_bob(vuln_app):
    return AuthPair(
        user_a_token=_login("alice", "alice123"),
        user_b_token=_login("bob", "bob123"),
        bola_test_ids={"user_a": [1, 3], "user_b": [2]},
    )


@pytest.fixture(scope="module")
def auth_pair_alice_admin(vuln_app):
    """For FLA tests: alice is low-priv, admin is high-priv.
    Even though admin is set as user_b here, FLA only uses
    fla_low_priv_user (user_a by default) — so this exercises
    alice trying to hit admin endpoints."""
    return AuthPair(
        user_a_token=_login("alice", "alice123"),
        user_b_token=_login("admin", "admin123"),
        # bola_test_ids not relevant for FLA tests
    )


# ---- BOLA: POSITIVE — alice reads bob's order via /vuln/orders/{id} ------

def test_bola_flags_vuln_orders_endpoint(vuln_app, auth_pair_alice_bob):
    """The textbook OWASP API1 case. Alice (user_a, owns 1+3) calls
    /vuln/orders/2 (bob's order) and gets 200 with bob's data back.
    Rule must produce a CRITICAL finding for this direction.
    """
    client = APIClient(base_url=vuln_app, auth_pair=auth_pair_alice_bob)
    op = OperationContext(
        method="GET", path="/vuln/orders/{order_id}",
        operation_id="vulnGetOrder", requires_auth=True,
    )
    findings = bola_rule.execute(client, op)
    crit = [f for f in findings if f.severity == Severity.CRITICAL]
    assert len(crit) == 2, \
        f"both directions of the diff should flag — got {len(crit)} CRITICAL"
    actors = {f.evidence["actor"] for f in crit}
    assert actors == {"user_a", "user_b"}, \
        f"expected both alice→bob's and bob→alice's directions to leak, got actors={actors}"


def test_bola_evidence_includes_real_target_id(vuln_app, auth_pair_alice_bob):
    """Sanity check the rule actually substituted bob's id (2) into
    the request path."""
    client = APIClient(base_url=vuln_app, auth_pair=auth_pair_alice_bob)
    op = OperationContext(
        method="GET", path="/vuln/orders/{order_id}",
        operation_id="vulnGetOrder", requires_auth=True,
    )
    findings = bola_rule.execute(client, op)
    alice_to_bob = next(f for f in findings
                        if f.evidence.get("actor") == "user_a"
                        and f.evidence.get("target_owner") == "user_b")
    assert alice_to_bob.evidence["target_id"] == 2
    assert alice_to_bob.evidence["probed_path"] == "/vuln/orders/2"
    # Response body should contain bob's order content
    assert "bob" in alice_to_bob.evidence["response_body_preview"]


# ---- BOLA: applies_to filtering on the safe endpoint ---------------------

def test_bola_does_not_apply_to_safe_me_orders(vuln_app):
    """/safe/me/orders has no path parameter so applies_to filters it
    out entirely — no probing happens, no findings emitted."""
    op = OperationContext(
        method="GET", path="/safe/me/orders",
        operation_id="safeMyOrders", requires_auth=True,
    )
    assert bola_rule.applies_to(op) is False


# ---- BOLA: skipped when no AuthPair --------------------------------------

def test_bola_skipped_when_no_auth_pair(vuln_app):
    """Running the rule without an AuthPair should INFO-skip, not
    crash and not false-fire."""
    client = APIClient(base_url=vuln_app, auth_pair=None)
    op = OperationContext(
        method="GET", path="/vuln/orders/{order_id}",
        operation_id="vulnGetOrder", requires_auth=True,
    )
    findings = bola_rule.execute(client, op)
    assert len(findings) == 1
    assert findings[0].severity == Severity.INFO


# ---- FLA: POSITIVE — alice hits /vuln/admin/users ------------------------

def test_fla_flags_vuln_admin_users(vuln_app, auth_pair_alice_admin):
    """The OWASP API5 case. The vuln admin endpoint requires auth
    but skips the role check, so alice (role=user) gets 200 + the
    full user list. Rule must produce a HIGH finding."""
    client = APIClient(base_url=vuln_app, auth_pair=auth_pair_alice_admin)
    op = OperationContext(
        method="GET", path="/vuln/admin/users",
        operation_id="vulnAdminUsers", requires_auth=True,
    )
    findings = function_authz_rule.execute(client, op)
    high = [f for f in findings if f.severity == Severity.HIGH]
    assert len(high) == 1
    assert high[0].rule_id.endswith("NonAdminAccessGranted")
    assert high[0].evidence["low_priv_user"] == "user_a"


# ---- FLA: NEGATIVE — alice gets 403 on /safe/admin/users ----------------

def test_fla_does_not_flag_safe_admin_users(vuln_app, auth_pair_alice_admin):
    """/safe/admin/users checks the role claim and 403s non-admins."""
    client = APIClient(base_url=vuln_app, auth_pair=auth_pair_alice_admin)
    op = OperationContext(
        method="GET", path="/safe/admin/users",
        operation_id="safeAdminUsers", requires_auth=True,
    )
    findings = function_authz_rule.execute(client, op)
    serious = [f for f in findings if f.severity != Severity.INFO]
    assert serious == [], \
        f"safe admin endpoint produced false positives: {[(f.rule_id, f.evidence) for f in serious]}"


# ---- FLA: non-admin paths produce no findings even when probed ---------

def test_fla_silent_on_non_admin_path(vuln_app, auth_pair_alice_admin):
    """The rule's path-pattern filter should keep it from probing
    non-admin endpoints entirely."""
    client = APIClient(base_url=vuln_app, auth_pair=auth_pair_alice_admin)
    op = OperationContext(
        method="GET", path="/safe/me/orders",
        operation_id="safeMyOrders", requires_auth=True,
    )
    findings = function_authz_rule.execute(client, op)
    assert findings == []
