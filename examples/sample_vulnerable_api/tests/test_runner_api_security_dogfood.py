"""End-to-end dogfood — full `run_api_security_scan` against the
deliberately-vulnerable Flask fixture.

This is the v0.8.0 culmination test. It boots the fixture, then runs
the actual MCP tool function (via `runner.api_security.run_scan`)
against `examples/sample_vulnerable_api/openapi.yaml`. Asserts:

  - The scan produces findings for EVERY in-scope OWASP category.
  - The scan returns the documented `security` block shape.
  - mass_assignment is OFF by default (opt-in confirmed).
  - Performance: full scan under 30 seconds (PRD §9).
  - No exceptions raised; consent / authorization gates honored.

This is the test that, when green, says "v0.8.0 ships."
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


@pytest.fixture
def auth_config(vuln_app):
    """Standard alice + bob auth pair with bola_test_ids matching
    the fixture's pre-seeded order ownership."""
    return {
        "token": _login("alice", "alice123"),
        "alt_user_token": _login("bob", "bob123"),
        "bola_test_ids": {"user_a": [1, 3], "user_b": [2]},
    }


# ---- The big one — full end-to-end scan -------------------------------

def test_full_scan_finds_all_4_default_owasp_categories(
    vuln_app, consent, auth_config
):
    """The pivotal test. Runs the scanner across the entire fixture
    and asserts each of the 4 DEFAULT-enabled OWASP categories
    produces at least one substantive finding.
    """
    result = run_scan(
        SPEC_PATH,
        auth=auth_config,
        base_url=vuln_app,
        # Default categories: headers + broken_auth + bola + function_authz.
        # mass_assignment intentionally NOT in defaults; tested separately
        # below.
        severity_threshold="low",  # surface LOW + HIGH + CRITICAL
        timeout_s=10,
    )

    assert "error" not in result, f"scan failed: {result}"

    rule_ids_fired = {f["rule_id"].split("-")[1] for f in result["findings"]
                       if "API" in f["rule_id"]}
    # Each OWASP category MUST have produced at least one finding.
    must_fire = {"API1", "API2", "API5", "API8"}
    assert must_fire.issubset(rule_ids_fired), \
        f"missing OWASP categories in findings: {must_fire - rule_ids_fired}; " \
        f"got {rule_ids_fired}"

    # Default categories must NOT include mass_assignment (API3).
    assert "API3" not in rule_ids_fired, \
        "mass_assignment fired by default — should be opt-in only"


def test_mass_assignment_runs_when_opted_in(vuln_app, consent, auth_config):
    """When the caller passes mass_assignment in `categories`, API3
    findings should appear."""
    result = run_scan(
        SPEC_PATH,
        auth=auth_config,
        base_url=vuln_app,
        categories=["mass_assignment"],
        severity_threshold="low",
    )
    assert "error" not in result
    api3_findings = [f for f in result["findings"]
                     if "OWASP-API3" in f["rule_id"]]
    assert len(api3_findings) > 0, \
        "mass_assignment opted in but no API3 findings produced"


def test_scan_returns_documented_schema(vuln_app, consent, auth_config):
    """Lock the report.json shape."""
    result = run_scan(SPEC_PATH, auth=auth_config, base_url=vuln_app)

    for key in ("scan_id", "spec_url", "base_url", "categories_run",
                "rules_ran", "ops_scanned", "severity_threshold",
                "findings", "summary"):
        assert key in result, f"missing key: {key}"

    assert isinstance(result["scan_id"], str) and len(result["scan_id"]) == 12
    assert isinstance(result["findings"], list)
    assert isinstance(result["summary"], dict)
    assert "total" in result["summary"]
    assert "by_severity" in result["summary"]
    for sev in ("critical", "high", "medium", "low", "info"):
        assert sev in result["summary"]["by_severity"]


def test_findings_are_sorted_by_severity(vuln_app, consent, auth_config):
    result = run_scan(
        SPEC_PATH, auth=auth_config, base_url=vuln_app,
        severity_threshold="info",  # include everything for ordering check
    )
    severities = [f["severity"] for f in result["findings"]]
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    ranks = [rank[s] for s in severities]
    assert ranks == sorted(ranks), \
        f"findings not sorted by severity: {severities[:10]}"


def test_finding_carries_evidence_and_remediation(vuln_app, consent, auth_config):
    """Every CRITICAL/HIGH finding must have non-empty evidence + remediation."""
    result = run_scan(SPEC_PATH, auth=auth_config, base_url=vuln_app,
                       severity_threshold="high")
    high_critical = [f for f in result["findings"]
                      if f["severity"] in ("critical", "high")]
    for f in high_critical:
        assert f["evidence"], f"empty evidence: {f['rule_id']}"
        assert f["remediation_hint"], f"empty remediation_hint: {f['rule_id']}"
        assert f["endpoint"], f"empty endpoint: {f['rule_id']}"


def test_scan_completes_within_perf_budget(vuln_app, consent, auth_config):
    """PRD §9 success criterion: full Tier 1 scan in under 30 seconds."""
    t0 = time.monotonic()
    result = run_scan(
        SPEC_PATH, auth=auth_config, base_url=vuln_app,
        categories=["headers", "broken_auth", "bola", "function_authz",
                    "mass_assignment"],  # FULL coverage
        severity_threshold="info",
    )
    elapsed = time.monotonic() - t0
    assert "error" not in result
    assert elapsed < 30.0, f"scan took {elapsed:.1f}s — over PRD §9's 30s budget"


# ---- consent + authorization gates honored end-to-end ----------------

def test_scan_refuses_without_consent_env(monkeypatch, vuln_app, auth_config):
    monkeypatch.delenv("QA_API_SECURITY_CONSENT", raising=False)
    result = run_scan(SPEC_PATH, auth=auth_config, base_url=vuln_app)
    assert result["error"] == "consent_required"


def test_scan_refuses_external_domain_without_allowlist(
    consent, monkeypatch, vuln_app, auth_config
):
    monkeypatch.delenv("QA_API_SECURITY_AUTHORIZED_DOMAINS", raising=False)
    result = run_scan(
        SPEC_PATH, auth=auth_config, base_url="https://prod.example.com",
    )
    assert result["error"] == "unauthorized_domain"


# ---- inversion: when AUTH is missing, BOLA + FLA INFO-skip cleanly --

def test_scan_with_no_auth_pair_skips_bola_and_fla_gracefully(vuln_app, consent):
    """Run with NO auth at all. headers + broken_auth still run but
    BOLA + FLA produce INFO skip findings rather than crashing."""
    result = run_scan(
        SPEC_PATH, auth=None, base_url=vuln_app,
        severity_threshold="info",
    )
    assert "error" not in result
    skipped = [f for f in result["findings"]
               if f["severity"] == "info" and "Skipped" in f["rule_id"]]
    bola_skips = [f for f in skipped if "API1-BOLA" in f["rule_id"]]
    assert len(bola_skips) >= 1, \
        "BOLA should emit at least one Skipped INFO when no auth pair given"
