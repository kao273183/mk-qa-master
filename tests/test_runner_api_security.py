"""Unit tests for `runners.api_security.run_scan`.

Covers the orchestration layer — consent gating, authorization, spec
parsing, category selection, severity threshold, error envelopes.
Real-HTTP dogfood lives in
`examples/sample_vulnerable_api/tests/test_runner_api_security_dogfood.py`.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from mk_qa_master.runners.api_security import (
    DEFAULT_CATEGORIES,
    RULE_BY_CATEGORY,
    _host_authorized,
    _walk_operations,
    load_spec,
    run_scan,
)


# ---- fixtures -------------------------------------------------------------

MINIMAL_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Test API", "version": "1.0"},
    "servers": [{"url": "http://localhost:9999"}],
    "paths": {
        "/foo": {
            "get": {"operationId": "getFoo", "responses": {"200": {"description": "OK"}}},
        },
        "/secure": {
            "get": {
                "operationId": "getSecure",
                "security": [{"bearerAuth": []}],
                "responses": {"200": {"description": "OK"}},
            },
        },
    },
}


@pytest.fixture
def with_consent(monkeypatch):
    monkeypatch.setenv("QA_API_SECURITY_CONSENT", "true")


@pytest.fixture
def spec_file(tmp_path):
    p = tmp_path / "spec.yaml"
    p.write_text(json.dumps(MINIMAL_SPEC))  # YAML loader handles JSON too
    return str(p)


# ---- consent gate --------------------------------------------------------

def test_run_scan_refuses_without_consent_env(monkeypatch, spec_file):
    monkeypatch.delenv("QA_API_SECURITY_CONSENT", raising=False)
    result = run_scan(spec_file)
    assert result["error"] == "consent_required"
    assert "QA_API_SECURITY_CONSENT" in result["consent_env"]


def test_run_scan_accepts_consent_true_lowercase(monkeypatch, spec_file):
    monkeypatch.setenv("QA_API_SECURITY_CONSENT", "true")
    result = run_scan(spec_file)
    assert "consent_required" not in str(result.get("error", ""))


@pytest.mark.parametrize("val", ["true", "TRUE", "1", "yes"])
def test_consent_accepts_truthy_values(monkeypatch, spec_file, val):
    monkeypatch.setenv("QA_API_SECURITY_CONSENT", val)
    result = run_scan(spec_file)
    assert result.get("error") != "consent_required"


# ---- authorization gate -------------------------------------------------

def test_localhost_implicitly_authorized():
    assert _host_authorized("http://localhost:5099") is True
    assert _host_authorized("http://127.0.0.1:5099") is True


def test_external_host_requires_allowlist(monkeypatch):
    monkeypatch.delenv("QA_API_SECURITY_AUTHORIZED_DOMAINS", raising=False)
    assert _host_authorized("https://api.example.com") is False


def test_external_host_authorized_via_env(monkeypatch):
    monkeypatch.setenv("QA_API_SECURITY_AUTHORIZED_DOMAINS",
                       "api.example.com, staging.acme.com")
    assert _host_authorized("https://api.example.com") is True
    assert _host_authorized("https://staging.acme.com") is True
    assert _host_authorized("https://other.example.com") is False


def test_run_scan_blocks_unauthorized_host(monkeypatch, tmp_path):
    monkeypatch.setenv("QA_API_SECURITY_CONSENT", "true")
    monkeypatch.delenv("QA_API_SECURITY_AUTHORIZED_DOMAINS", raising=False)
    spec = {**MINIMAL_SPEC, "servers": [{"url": "https://external.example.com"}]}
    p = tmp_path / "spec.json"
    p.write_text(json.dumps(spec))
    result = run_scan(str(p))
    assert result["error"] == "unauthorized_domain"
    assert result["host"] == "external.example.com"


# ---- spec loader --------------------------------------------------------

def test_load_spec_json_file(spec_file):
    spec = load_spec(spec_file)
    assert spec["openapi"].startswith("3.")
    assert "/foo" in spec["paths"]


def test_load_spec_yaml_string(tmp_path):
    p = tmp_path / "spec.yaml"
    p.write_text("openapi: 3.0.0\ninfo: {title: T, version: '1'}\npaths: {}\n")
    spec = load_spec(str(p))
    assert spec["openapi"] == "3.0.0"


def test_load_spec_file_uri(spec_file):
    spec = load_spec(f"file://{spec_file}")
    assert "/foo" in spec["paths"]


def test_load_spec_unknown_scheme_raises():
    with pytest.raises(ValueError):
        load_spec("ftp://example.com/spec.yaml")


# ---- operation walker --------------------------------------------------

def test_walk_operations_extracts_methods():
    ops = _walk_operations(MINIMAL_SPEC)
    by_endpoint = {(op.method, op.path) for op in ops}
    assert ("GET", "/foo") in by_endpoint
    assert ("GET", "/secure") in by_endpoint


def test_walk_operations_marks_requires_auth():
    ops = _walk_operations(MINIMAL_SPEC)
    secure = next(op for op in ops if op.path == "/secure")
    foo = next(op for op in ops if op.path == "/foo")
    assert secure.requires_auth is True
    assert foo.requires_auth is False


def test_walk_operations_skips_non_http_keys():
    """Path items can contain `parameters`, `summary`, etc. — not HTTP methods."""
    spec = {
        "paths": {
            "/x": {
                "summary": "shared summary",
                "parameters": [{"name": "p", "in": "query"}],
                "get": {"operationId": "x"},
            }
        }
    }
    ops = _walk_operations(spec)
    assert len(ops) == 1
    assert ops[0].method == "GET"


def test_walk_operations_resolves_simple_refs():
    """$ref in requestBody schema should be inlined so rules see the actual shape."""
    spec = {
        "components": {
            "schemas": {
                "SignupReq": {
                    "type": "object",
                    "required": ["u", "p"],
                    "properties": {
                        "u": {"type": "string"},
                        "p": {"type": "string"},
                    },
                },
            },
        },
        "paths": {
            "/signup": {
                "post": {
                    "operationId": "signup",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/SignupReq"},
                            }
                        }
                    },
                },
            },
        },
    }
    ops = _walk_operations(spec)
    op = ops[0]
    schema = op.spec["requestBody"]["content"]["application/json"]["schema"]
    assert schema["type"] == "object"
    assert "u" in schema["properties"]


# ---- run_scan happy path (no rules actually fire) ------------------------

def test_run_scan_returns_well_formed_report(with_consent, spec_file):
    """Against an empty mock target, no rules should fire — but the
    return shape MUST be the documented schema."""
    # Override base_url with a localhost address — and the rules will
    # fail to connect, producing INFO probe-failed findings. Those
    # are below the default `medium` threshold so they get filtered.
    result = run_scan(spec_file, base_url="http://localhost:1")
    assert "findings" in result
    assert "summary" in result
    assert "scan_id" in result
    assert isinstance(result["scan_id"], str) and len(result["scan_id"]) == 12
    assert result["ops_scanned"] == 2  # /foo + /secure
    assert "headers" in result["categories_run"]
    assert result["severity_threshold"] == "medium"
    # Probe failures are INFO → below `medium` threshold → filtered.
    assert result["findings"] == []
    assert result["findings_below_threshold_count"] >= 0


# ---- category selection -------------------------------------------------

def test_run_scan_default_categories_exclude_mass_assignment(with_consent, spec_file):
    result = run_scan(spec_file, base_url="http://localhost:1")
    assert "mass_assignment" not in result["categories_run"]
    assert set(result["categories_run"]) == set(DEFAULT_CATEGORIES)


def test_run_scan_explicit_categories_can_include_mass_assignment(with_consent, spec_file):
    result = run_scan(
        spec_file, base_url="http://localhost:1",
        categories=["mass_assignment", "headers"],
    )
    assert result["categories_run"] == ["mass_assignment", "headers"]


def test_run_scan_rejects_unknown_category(with_consent, spec_file):
    result = run_scan(
        spec_file, base_url="http://localhost:1",
        categories=["headers", "nonexistent_rule"],
    )
    assert result["error"] == "unknown_categories"


# ---- severity threshold ------------------------------------------------

def test_run_scan_rejects_invalid_severity(with_consent, spec_file):
    result = run_scan(
        spec_file, base_url="http://localhost:1",
        severity_threshold="urgent",
    )
    assert result["error"] == "bad_severity_threshold"


@pytest.mark.parametrize("threshold", ["critical", "high", "medium", "low", "info"])
def test_run_scan_accepts_all_valid_severities(with_consent, spec_file, threshold):
    result = run_scan(
        spec_file, base_url="http://localhost:1",
        severity_threshold=threshold,
    )
    assert result["severity_threshold"] == threshold


# ---- bad inputs --------------------------------------------------------

def test_run_scan_spec_load_failure_yields_error_envelope(with_consent):
    result = run_scan("/this/spec/does/not/exist.yaml")
    assert result["error"] == "spec_load_failed"
    assert "hint" in result


def test_run_scan_no_base_url_anywhere_yields_error(with_consent, tmp_path):
    """Spec without servers + no override base_url → error."""
    spec = {"openapi": "3.0.0", "info": {"title": "T", "version": "1"},
            "paths": {}}
    p = tmp_path / "no-servers.json"
    p.write_text(json.dumps(spec))
    result = run_scan(str(p))
    assert result["error"] == "no_base_url"
