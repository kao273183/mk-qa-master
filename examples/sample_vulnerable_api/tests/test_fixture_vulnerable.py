"""Smoke test — proves the sample_vulnerable_api fixture is actually vulnerable.

Per the v0.8 mobile postmortem (`docs/v0.8-mobile-postmortem.md`), the
single biggest lesson was: don't trust an `rc == 0`-style validation
when claiming a capability works. This test exists to enforce that
lesson for the v0.8 API security PRD's Tier 1 ground truth.

What this test asserts:

  - The Flask app boots and answers /health.
  - For each of the 5 in-scope OWASP categories:
      * the `/vuln/...` endpoint exhibits the vulnerable behavior end
        to end (real HTTP request, real response body asserted)
      * the `/safe/...` endpoint exhibits the safe behavior

If any of these fail, the fixture itself is broken and no downstream
rule PR can rely on it. This must stay green as PRs 2-5 land.

Run:
  pytest examples/sample_vulnerable_api/tests/test_fixture_vulnerable.py
"""
from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import jwt as pyjwt
import pytest
import requests

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
PORT = 5099
BASE_URL = f"http://127.0.0.1:{PORT}"
JWT_SECRET = "vulnerable-api-test-secret-do-not-deploy"


# ---- App lifecycle ---------------------------------------------------------

def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        return s.connect_ex(("127.0.0.1", port)) == 0


@pytest.fixture(scope="module")
def vuln_app():
    """Boot the Flask app in a subprocess for the duration of this module.

    A module-scoped fixture (rather than per-test) keeps the test suite
    under 5 s. Each test uses /_reset between cases for state isolation.
    """
    if _port_open(PORT):
        pytest.skip(f"Port {PORT} already in use; aborting to avoid clobber.")

    proc = subprocess.Popen(
        [sys.executable, str(APP_PATH)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    try:
        # Poll /health up to 5 s.
        deadline = time.time() + 5.0
        while time.time() < deadline:
            try:
                r = requests.get(f"{BASE_URL}/health", timeout=0.5)
                if r.status_code == 200:
                    break
            except requests.RequestException:
                pass
            time.sleep(0.1)
        else:
            stdout, stderr = proc.communicate(timeout=1)
            raise RuntimeError(
                f"Flask app didn't become ready in 5s.\n"
                f"stdout:\n{stdout.decode(errors='replace')}\n"
                f"stderr:\n{stderr.decode(errors='replace')}"
            )
        yield BASE_URL
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(autouse=True)
def _reset_state(vuln_app):
    requests.post(f"{vuln_app}/_reset", timeout=1).raise_for_status()


def _login(username: str, password: str) -> str:
    r = requests.post(
        f"{BASE_URL}/login",
        json={"username": username, "password": password},
        timeout=2,
    )
    r.raise_for_status()
    return r.json()["token"]


# ---- OWASP API1 (BOLA) — vuln/safe pair ------------------------------------

def test_vuln_bola_returns_other_users_order(vuln_app):
    """Alice can read bob's order via /vuln/orders/{id}. This MUST be true."""
    alice = _login("alice", "alice123")
    r = requests.get(
        f"{vuln_app}/vuln/orders/2",  # order 2 belongs to bob
        headers={"Authorization": f"Bearer {alice}"},
        timeout=2,
    )
    assert r.status_code == 200, "vuln endpoint should return 200 for any auth'd user"
    body = r.json()
    assert body["owner_id"] == 2, "fixture broken: order id 2 should belong to bob"
    assert body["item"] == "bob's pizza", \
        "alice MUST be able to see bob's order content for BOLA to be demonstrable"


def test_safe_my_orders_returns_only_callers_orders(vuln_app):
    """Alice's /safe/me/orders contains only her orders (1 and 3)."""
    alice = _login("alice", "alice123")
    r = requests.get(
        f"{vuln_app}/safe/me/orders",
        headers={"Authorization": f"Bearer {alice}"},
        timeout=2,
    )
    assert r.status_code == 200
    owner_ids = {o["owner_id"] for o in r.json()["orders"]}
    assert owner_ids == {1}, f"safe endpoint leaked cross-user data: {owner_ids}"


# ---- OWASP API2 (Broken Auth) ----------------------------------------------

def _forge_alg_none_jwt(claims: dict) -> str:
    """Hand-roll an `alg: none` JWT — PyJWT 2.x refuses to encode these."""
    header = {"alg": "none", "typ": "JWT"}
    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(
            json.dumps(d, separators=(",", ":")).encode()
        ).rstrip(b"=").decode()
    return f"{b64(header)}.{b64(claims)}."  # trailing dot = empty signature


def test_vuln_profile_accepts_alg_none_admin_forgery(vuln_app):
    """Scanner submits a forged alg:none token claiming admin role.

    Real OWASP API2 textbook case. /vuln/profile must accept it and
    return the forged claims.
    """
    forged = _forge_alg_none_jwt({
        "sub": 999, "username": "attacker", "role": "admin",
        "exp": int(time.time()) + 60,
    })
    r = requests.get(
        f"{vuln_app}/vuln/profile",
        headers={"Authorization": f"Bearer {forged}"},
        timeout=2,
    )
    assert r.status_code == 200, "vuln endpoint should accept forged token"
    assert r.json()["role"] == "admin", "forged role MUST surface for the test to mean anything"


def test_vuln_profile_accepts_expired_token(vuln_app):
    """Expired token also gets through the vulnerable decoder."""
    expired = pyjwt.encode(
        {"sub": 1, "role": "user", "username": "alice",
         "exp": int(time.time()) - 3600,  # 1h ago
         "iat": int(time.time()) - 7200},
        JWT_SECRET,
        algorithm="HS256",
    )
    r = requests.get(
        f"{vuln_app}/vuln/profile",
        headers={"Authorization": f"Bearer {expired}"},
        timeout=2,
    )
    assert r.status_code == 200, "vuln endpoint should accept expired token"


def test_safe_profile_rejects_alg_none(vuln_app):
    forged = _forge_alg_none_jwt({
        "sub": 999, "role": "admin", "exp": int(time.time()) + 60,
    })
    r = requests.get(
        f"{vuln_app}/safe/profile",
        headers={"Authorization": f"Bearer {forged}"},
        timeout=2,
    )
    assert r.status_code == 401, "safe endpoint MUST reject alg:none forgery"


def test_safe_profile_rejects_expired(vuln_app):
    expired = pyjwt.encode(
        {"sub": 1, "role": "user", "exp": int(time.time()) - 3600},
        JWT_SECRET,
        algorithm="HS256",
    )
    r = requests.get(
        f"{vuln_app}/safe/profile",
        headers={"Authorization": f"Bearer {expired}"},
        timeout=2,
    )
    assert r.status_code == 401, "safe endpoint MUST reject expired token"


# ---- OWASP API3 (Mass Assignment) ------------------------------------------

def test_vuln_signup_persists_role_admin_tampering(vuln_app):
    """POST /vuln/signup with a `role: admin` field — must be persisted."""
    r = requests.post(
        f"{vuln_app}/vuln/signup",
        json={"username": "mallory", "password": "pwd",
              "role": "admin", "is_verified": True},
        timeout=2,
    )
    assert r.status_code == 201
    assert r.json().get("role") == "admin", \
        "vuln endpoint MUST echo back the tampered role for the rule to detect it"
    # And really persisted — not just echoed.
    persisted = requests.get(f"{vuln_app}/_inspect/signups", timeout=2).json()["signups"]
    assert any(s.get("username") == "mallory" and s.get("role") == "admin"
               for s in persisted), "tampered field MUST persist"


def test_safe_signup_silently_drops_extras(vuln_app):
    r = requests.post(
        f"{vuln_app}/safe/signup",
        json={"username": "alice2", "password": "pwd", "role": "admin"},
        timeout=2,
    )
    assert r.status_code == 201
    assert r.json().get("role") == "user", \
        "safe endpoint MUST default role to 'user', not honor the tampered value"


# ---- OWASP API5 (Function Level Authz) ------------------------------------

def test_vuln_admin_users_accessible_to_regular_user(vuln_app):
    """Alice (role=user) can list users via /vuln/admin/users — bug."""
    alice = _login("alice", "alice123")
    r = requests.get(
        f"{vuln_app}/vuln/admin/users",
        headers={"Authorization": f"Bearer {alice}"},
        timeout=2,
    )
    assert r.status_code == 200, "vuln admin endpoint MUST grant access to non-admin"
    assert any(u["role"] == "admin" for u in r.json()["users"]), \
        "the leak must include admin records to demonstrate the breach"


def test_safe_admin_users_blocks_regular_user(vuln_app):
    alice = _login("alice", "alice123")
    r = requests.get(
        f"{vuln_app}/safe/admin/users",
        headers={"Authorization": f"Bearer {alice}"},
        timeout=2,
    )
    assert r.status_code == 403, "safe admin endpoint MUST 403 non-admins"


def test_safe_admin_users_allows_admin(vuln_app):
    admin = _login("admin", "admin123")
    r = requests.get(
        f"{vuln_app}/safe/admin/users",
        headers={"Authorization": f"Bearer {admin}"},
        timeout=2,
    )
    assert r.status_code == 200


# ---- OWASP API8 (Security Misconfiguration) -------------------------------

def test_vuln_data_loose_cors_and_missing_headers(vuln_app):
    r = requests.get(f"{vuln_app}/vuln/data", timeout=2)
    assert r.status_code == 200
    assert r.headers.get("Access-Control-Allow-Origin") == "*"
    assert r.headers.get("Access-Control-Allow-Credentials") == "true", \
        "the dangerous combo (wildcard + credentials) MUST be present"
    # Missing security headers — verify ALL are absent.
    for missing in ("Strict-Transport-Security", "Content-Security-Policy",
                    "X-Content-Type-Options", "X-Frame-Options"):
        assert missing not in r.headers, \
            f"vuln endpoint MUST NOT set {missing}; got {r.headers.get(missing)}"


def test_vuln_data_leaks_stack_trace_on_crash(vuln_app):
    r = requests.get(f"{vuln_app}/vuln/data?crash=1", timeout=2)
    assert r.status_code == 500
    assert "Traceback" in r.text, "vuln 500 body MUST contain a traceback"
    assert "postgres://" in r.text, "vuln 500 body MUST leak a credential-shaped string"


def test_safe_data_strict_cors_and_full_headers(vuln_app):
    r = requests.get(f"{vuln_app}/safe/data", timeout=2)
    assert r.status_code == 200
    assert r.headers.get("Access-Control-Allow-Origin") == "https://app.example.com"
    for required in ("Strict-Transport-Security", "Content-Security-Policy",
                     "X-Content-Type-Options", "X-Frame-Options"):
        assert required in r.headers, \
            f"safe endpoint MUST set {required}"


def test_safe_data_generic_error_on_crash(vuln_app):
    r = requests.get(f"{vuln_app}/safe/data?crash=1", timeout=2)
    assert r.status_code == 500
    assert "Traceback" not in r.text
    assert "postgres" not in r.text
