"""Dogfood test — runs broken_auth rule against the Tier 1 fixture.

Per v0.8.0 PRD §10 (mobile-postmortem lesson applied): proves the
rule actually catches the API2 ground truth on /vuln/profile and
does NOT fire on /safe/profile.

Recall the fixture's vuln vs safe profile:
  - /vuln/profile: uses `_decode_loose` — verify_signature=False,
    verify_exp=False, algorithms=['none', HS256]. Accepts:
      * alg:none tokens (alg whitelisted)
      * any-signature tokens (signature not verified)
    Rejects:
      * no Authorization header (early return)
      * malformed JWTs (PyJWTError on decode)
  - /safe/profile: full validation via `_decode_strict`. Rejects
    all 4 probes.
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
    broken_auth_rule,
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


def _client(base_url: str) -> APIClient:
    return APIClient(base_url=base_url, timeout_s=5.0)


# ---- POSITIVE: rule must catch /vuln/profile ----------------------------

def test_rule_flags_vuln_profile_for_alg_none(vuln_app):
    """The vuln endpoint's `_decode_loose` whitelists alg:none, so
    a forged alg:none admin token returns 200. AlgNone finding must
    fire at HIGH severity.
    """
    op = OperationContext(
        method="GET", path="/vuln/profile",
        operation_id="vulnGetProfile", requires_auth=True,
    )
    findings = broken_auth_rule.execute(_client(vuln_app), op)
    probes_flagged = {f.evidence["probe"] for f in findings
                       if "probe" in f.evidence and f.severity != Severity.INFO}
    assert "AlgNone" in probes_flagged, \
        f"AlgNone must be flagged on /vuln/profile, got {probes_flagged}"
    alg_none = next(f for f in findings
                    if f.evidence.get("probe") == "AlgNone")
    assert alg_none.severity == Severity.HIGH


def test_rule_flags_vuln_profile_for_wrong_signature(vuln_app):
    """_decode_loose sets verify_signature=False, so a wrong-key-signed
    token also returns 200. WrongSignature finding must fire at HIGH.
    """
    op = OperationContext(
        method="GET", path="/vuln/profile",
        operation_id="vulnGetProfile", requires_auth=True,
    )
    findings = broken_auth_rule.execute(_client(vuln_app), op)
    wrong_sig = [f for f in findings if f.evidence.get("probe") == "WrongSignature"]
    assert len(wrong_sig) == 1
    assert wrong_sig[0].severity == Severity.HIGH


def test_rule_does_NOT_flag_no_auth_on_vuln_profile(vuln_app):
    """Even the vulnerable endpoint requires SOMETHING in the
    Authorization header (early-return on missing token). So NoAuth
    probe should NOT fire on /vuln/profile — it would on a more
    broken endpoint, but not this one. Negative test.
    """
    op = OperationContext(
        method="GET", path="/vuln/profile",
        operation_id="vulnGetProfile", requires_auth=True,
    )
    findings = broken_auth_rule.execute(_client(vuln_app), op)
    no_auth = [f for f in findings if f.evidence.get("probe") == "NoAuth"
               and f.severity != Severity.INFO]
    assert no_auth == [], \
        f"NoAuth should not fire here — vuln/profile requires a token to enter"


# ---- NEGATIVE: rule must NOT fire on /safe/profile ----------------------

def test_rule_does_not_flag_safe_profile(vuln_app):
    """/safe/profile uses `_decode_strict` — signature, alg, exp all
    validated. ALL four probes must be rejected, ZERO findings
    (other than maybe INFO from probe failures, which there shouldn't
    be against a healthy fixture).
    """
    op = OperationContext(
        method="GET", path="/safe/profile",
        operation_id="safeGetProfile", requires_auth=True,
    )
    findings = broken_auth_rule.execute(_client(vuln_app), op)
    serious = [f for f in findings if f.severity != Severity.INFO]
    assert serious == [], \
        f"safe endpoint produced false positives: " \
        f"{[(f.rule_id, f.evidence.get('probe')) for f in serious]}"


# ---- Doesn't apply to no-auth endpoints --------------------------------

def test_rule_does_not_apply_to_health_endpoint(vuln_app):
    """`/health` has no auth requirement — applies_to() must return
    False so we never even probe it (and never false-positive when
    health returns 200 with no token)."""
    op = OperationContext(
        method="GET", path="/health",
        operation_id="health", requires_auth=False,
    )
    assert broken_auth_rule.applies_to(op) is False
