"""v0.9.4 dogfood — full prelude→scan→verify against the Tier 1
vulnerable Flask app in a single tool call.

This is the closing test of the v0.9.x narrative. It demonstrates
the end-to-end bookend pattern Webwright's plan.md taught us:

  qa_plan(critical_points)
     ↓
  run_api_security_scan(spec, plan_id=...)
     ↓ (internal verify_plan call)
  response includes plan_verification with per-CP pass/fail
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

from mk_qa_master.runners.api_security import run_scan
from mk_qa_master.tools.qa_plan import _reset_cache_for_tests, qa_plan_tool

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
SPEC_PATH = str(Path(__file__).resolve().parents[1] / "openapi.yaml")
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


@pytest.fixture
def consent(monkeypatch):
    monkeypatch.setenv("QA_API_SECURITY_CONSENT", "true")


@pytest.fixture(autouse=True)
def _isolate_qa_plan_cache(monkeypatch):
    _reset_cache_for_tests()
    # Keep persistence OFF for these tests so they're hermetic.
    monkeypatch.delenv("QA_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("QA_PLAN_PERSIST", raising=False)
    yield
    _reset_cache_for_tests()


@pytest.fixture
def auth_config(vuln_app):
    return {
        "token": _login("alice", "alice123"),
        "alt_user_token": _login("bob", "bob123"),
        "bola_test_ids": {"user_a": [1, 3], "user_b": [2]},
    }


# ---- the pivotal one — end-to-end prelude→scan→auto-verify ------------

def test_full_prelude_scan_verify_chain(vuln_app, consent, auth_config):
    """Declare CPs that mirror the fixture's known vulnerabilities,
    run the scan with plan_id, and assert every CP is satisfied by
    the resulting findings."""
    plan = qa_plan_tool({
        "task": "Find all expected OWASP findings against /vuln/ endpoints",
        "kind": "scan",
        "critical_points": [
            # API1 — alice reads bob's order via /vuln/orders/{id}
            {"id": "CP-API1",
             "description": "BOLA on /vuln/orders/{id}",
             "verification_hint": "OWASP-API1-BOLA-CrossUserDataExposure"},
            # API2 — alg:none accepted on /vuln/profile
            {"id": "CP-API2-Alg",
             "description": "alg:none accepted on /vuln/profile",
             "verification_hint": "OWASP-API2-BrokenAuth-AlgNone"},
            # API5 — alice gets 200 on /vuln/admin/users
            {"id": "CP-API5",
             "description": "Non-admin access to /vuln/admin/users",
             "verification_hint": "OWASP-API5-FunctionAuthz-NonAdminAccessGranted"},
            # API8 — missing security headers on /vuln/data
            {"id": "CP-API8-Headers",
             "description": "Missing security headers on /vuln/data",
             "verification_hint": "OWASP-API8-Headers-MissingHeader"},
            # API8 — wildcard CORS with credentials
            {"id": "CP-API8-CORS",
             "description": "Wildcard CORS + credentials on /vuln/data",
             "verification_hint": "OWASP-API8-Headers-CORSWildcardWithCredentials"},
        ],
    })

    result = run_scan(
        SPEC_PATH,
        auth=auth_config,
        base_url=vuln_app,
        severity_threshold="low",  # so we see the LOW + HIGH CORS findings
        plan_id=plan["plan_id"],
    )

    assert "error" not in result, f"scan failed: {result}"
    assert "plan_verification" in result, "v0.9.4 missing the new field"

    pv = result["plan_verification"]
    assert pv["status"] == "passed", (
        f"expected all 5 CPs satisfied; got status={pv['status']!r}, "
        f"unmet={pv['unmet']}"
    )
    assert pv["summary"]["total"] == 5
    assert pv["summary"]["satisfied"] == 5


def test_unmet_cp_surfaced_in_plan_verification(vuln_app, consent, auth_config):
    """A CP that doesn't correspond to any real finding shows up as
    unmet — proves the verifier isn't auto-passing."""
    plan = qa_plan_tool({
        "task": "Mixed real + fake CPs",
        "critical_points": [
            {"id": "REAL", "description": "BOLA",
             "verification_hint": "OWASP-API1-BOLA"},
            {"id": "FAKE", "description": "Quantum vulnerability",
             "verification_hint": "OWASP-QUANTUM-EntanglementLeak"},
        ],
    })
    result = run_scan(
        SPEC_PATH, auth=auth_config, base_url=vuln_app,
        plan_id=plan["plan_id"],
    )
    pv = result["plan_verification"]
    assert pv["status"] == "incomplete"
    assert pv["unmet"] == ["FAKE"]


def test_plan_source_memory_when_just_created(vuln_app, consent, auth_config):
    """The plan was created moments ago — verify_plan should hit
    in-memory cache, not disk."""
    plan = qa_plan_tool({"task": "x", "critical_points": ["y"]})
    result = run_scan(
        SPEC_PATH, auth=auth_config, base_url=vuln_app,
        plan_id=plan["plan_id"],
    )
    assert result["plan_verification"]["plan_source"] == "memory"


def test_response_keys_present(vuln_app, consent, auth_config):
    """Lock the response shape so future PRs can't accidentally drop
    fields the host LLM relies on."""
    plan = qa_plan_tool({"task": "shape lock", "critical_points": ["x"]})
    result = run_scan(SPEC_PATH, auth=auth_config, base_url=vuln_app,
                       plan_id=plan["plan_id"])
    # v0.8.0 fields still there
    for key in ("scan_id", "findings", "summary"):
        assert key in result
    # v0.9.4 field
    assert "plan_verification" in result
    pv = result["plan_verification"]
    for key in ("plan_id", "status", "checklist", "unmet", "summary",
                "evidence_sources", "plan_source", "verified_at"):
        assert key in pv


def test_threshold_filtering_affects_verify(vuln_app, consent, auth_config):
    """With threshold='high', the LOW CORS finding is filtered out,
    so a CP targeting the LOW variant comes back unmet."""
    plan = qa_plan_tool({
        "task": "x",
        "critical_points": [
            {"id": "LOW_TARGETED",
             "description": "wildcard CORS without credentials",
             "verification_hint": "OWASP-API8-Headers-CORSWildcardOrigin"},
        ],
    })
    # threshold='high' — LOW finding gets filtered before verify sees it
    result = run_scan(
        SPEC_PATH, auth=auth_config, base_url=vuln_app,
        severity_threshold="high",
        plan_id=plan["plan_id"],
    )
    # Either the CP is unmet OR the finding doesn't exist at high
    # threshold — either way, status is not 'passed'.
    assert result["plan_verification"]["status"] != "passed"
