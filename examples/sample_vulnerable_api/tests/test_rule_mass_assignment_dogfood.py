"""Dogfood test — runs mass_assignment rule against the Tier 1 fixture.

Per PRD §10. Asserts:
  - /vuln/signup echoes back the entire request body (every dangerous
    field comes back at the sent value) → multiple HIGH findings
  - /safe/signup forces role=user and doesn't echo extras → no
    HIGH findings (some INFO inconclusive findings are OK and
    expected for the fields the safe endpoint doesn't return at all)

The fixture's vuln vs safe signup behavior (from app.py):

  /vuln/signup:
      record = dict(body)              # ← every field as-is
      record["id"] = ...
      _SIGNUPS.append(record)
      return jsonify(record), 201       # ← echoes everything

  /safe/signup:
      record = {
          "id": ...,
          "username": body["username"],
          "password": body["password"],
          "role": "user",                # ← forced
      }
      _SIGNUPS.append(record)
      return jsonify(record), 201       # ← echoes only the whitelist
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
    OperationContext,
    Severity,
    mass_assignment_rule,
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


@pytest.fixture(autouse=True)
def _reset_state(vuln_app):
    requests.post(f"{vuln_app}/_reset", timeout=1).raise_for_status()


def _signup_op(path: str) -> OperationContext:
    """OpenAPI op for the vuln/safe signup endpoints. Schema mirrors
    the fixture's openapi.yaml — required fields = username + password.
    """
    return OperationContext(
        method="POST", path=path,
        operation_id="signup", requires_auth=False,
        spec={
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["username", "password"],
                            "additionalProperties": False,
                            "properties": {
                                "username": {"type": "string"},
                                "password": {"type": "string"},
                            },
                        }
                    }
                }
            }
        },
    )


# ---- POSITIVE: /vuln/signup ---------------------------------------------

def test_vuln_signup_flagged_for_role_admin(vuln_app):
    """The textbook OWASP API3 case. The vuln endpoint does
    `record = dict(body)` so every field we send comes back at the
    same value — including `role: "admin"`."""
    client = APIClient(base_url=vuln_app)
    findings = mass_assignment_rule.execute(client, _signup_op("/vuln/signup"))
    high = [f for f in findings if f.severity == Severity.HIGH]
    role_findings = [f for f in high if f.evidence.get("field") == "role"]
    assert len(role_findings) == 1, \
        f"expected exactly 1 HIGH finding for `role` on /vuln/signup, got {len(role_findings)}"
    assert role_findings[0].evidence["sent_value"] == "admin"
    assert role_findings[0].evidence["echoed_value"] == "admin"


def test_vuln_signup_flagged_for_multiple_dangerous_fields(vuln_app):
    """Every default dangerous field should fire because the vuln
    endpoint echoes EVERYTHING."""
    client = APIClient(base_url=vuln_app)
    findings = mass_assignment_rule.execute(client, _signup_op("/vuln/signup"))
    high = [f for f in findings if f.severity == Severity.HIGH]
    flagged_fields = {f.evidence["field"] for f in high}
    # We expect AT LEAST the headline ones to fire
    must_flag = {"role", "is_admin", "isAdmin", "is_verified", "verified"}
    assert must_flag.issubset(flagged_fields), \
        f"must-flag fields missing from /vuln/signup findings: " \
        f"{must_flag - flagged_fields}"


def test_vuln_signup_persistence_actually_happened(vuln_app):
    """Sanity check: confirm via the fixture's /_inspect/signups that
    the dangerous role IS persisted. If this fails, the FIXTURE is
    broken before any scanner conclusions are valid."""
    # The rule's run already created records; just verify state.
    client = APIClient(base_url=vuln_app)
    mass_assignment_rule.execute(client, _signup_op("/vuln/signup"))
    persisted = requests.get(f"{vuln_app}/_inspect/signups", timeout=2).json()["signups"]
    has_admin_role = any(s.get("role") == "admin" for s in persisted)
    assert has_admin_role, \
        "fixture broke: no persisted record has role=admin after the scanner ran"


# ---- NEGATIVE: /safe/signup ---------------------------------------------

def test_safe_signup_no_high_findings(vuln_app):
    """The safe endpoint forces role=user and only persists whitelisted
    fields. Zero HIGH findings should fire."""
    client = APIClient(base_url=vuln_app)
    findings = mass_assignment_rule.execute(client, _signup_op("/safe/signup"))
    high = [f for f in findings if f.severity == Severity.HIGH]
    assert high == [], \
        f"safe signup produced false-positive HIGH findings: " \
        f"{[(f.evidence['field'], f.evidence.get('echoed_value')) for f in high]}"


def test_safe_signup_role_override_classified_as_safe(vuln_app):
    """Specifically the `role: admin` probe should be classified as
    safe — server echoes `role: user`, which is exactly the
    'safe_overridden' branch of the classifier."""
    # Send the same probe the rule would send, manually, so we can
    # see what the server returns.
    resp = requests.post(
        f"{vuln_app}/safe/signup",
        json={"username": "scan-test", "password": "scan-test", "role": "admin"},
        timeout=2,
    )
    assert resp.status_code == 201
    body = resp.json()
    # Server should have FORCED role to "user", not the "admin" we sent.
    assert body.get("role") == "user", \
        f"safe endpoint regressed — it echoed back role={body.get('role')!r} " \
        f"instead of forcing 'user'"


# ---- applies_to filtering -----------------------------------------------

def test_does_not_apply_to_get_endpoints(vuln_app):
    """GET endpoints have no body schema → applies_to False → no
    findings even when called directly."""
    op = OperationContext(
        method="GET", path="/safe/data",
        operation_id="safeData", requires_auth=False, spec={},
    )
    assert mass_assignment_rule.applies_to(op) is False
