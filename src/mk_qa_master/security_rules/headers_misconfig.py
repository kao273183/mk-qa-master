"""OWASP API8 — Security Misconfiguration (response headers subset).

PR-2 of the v0.8.0 rollout. The simplest in-scope rule: no auth state
needed, single HTTP probe per endpoint, deterministic findings.

What this rule checks
---------------------

For each GET operation, fetch the endpoint once and inspect the
response headers. Reports a finding for:

  - Missing `Strict-Transport-Security`          (medium)
  - Missing `Content-Security-Policy`            (medium)
  - Missing `X-Content-Type-Options: nosniff`    (medium)
  - Missing `X-Frame-Options`                    (medium)
  - `Access-Control-Allow-Origin: *` with
    `Access-Control-Allow-Credentials: true`     (high — dangerous combo)
  - `Access-Control-Allow-Origin: *` alone       (low)

Verbose-error responses (stack traces in 5xx bodies) are NOT in this
rule's scope — they're a separate API8 sub-issue and warrant their
own rule with safer probing (you don't want to trigger 500s casually
on production endpoints). Filed as a follow-up.

Why GET-only
------------

For PR-2, restricting to GET keeps probing safe-by-default — a
header-check rule should never mutate server state. POST/PUT/PATCH/
DELETE endpoints have legitimate reasons for different header
expectations (CSP often loosened on JSON APIs that don't render
HTML) and benefit from a richer rule. Future PR.

Self-test
---------

`examples/sample_vulnerable_api/`'s `/vuln/data` is the positive
target: missing all 4 required headers + the wildcard-with-credentials
combo. `/safe/data` is the negative: all 4 headers set + restricted
origin. The dogfood test at
`examples/sample_vulnerable_api/tests/test_rule_headers_dogfood.py`
runs the rule against both and asserts behavior end-to-end.
"""
from __future__ import annotations

from typing import Any

from .base import APIClient, Finding, OperationContext, Severity


# Required response headers — missing ANY of these triggers a finding.
# Capitalization mirrors what servers conventionally send; requests
# normalizes headers case-insensitively so the case below is purely
# for the finding output.
REQUIRED_HEADERS: list[str] = [
    "Strict-Transport-Security",
    "Content-Security-Policy",
    "X-Content-Type-Options",
    "X-Frame-Options",
]


class HeadersMisconfigRule:
    """Concrete rule. Conforms to `SecurityRule` Protocol structurally."""

    id: str = "OWASP-API8-Headers"
    severity: Severity = Severity.MEDIUM
    requires_auth_pair: bool = False

    def applies_to(self, op: OperationContext) -> bool:
        # GET only for PR-2 — keeps probing safe-by-default.
        return op.method.upper() == "GET"

    def execute(self, client: APIClient, op: OperationContext) -> list[Finding]:
        endpoint = f"GET {op.path}"
        # If the endpoint requires auth and we have no token, we can't
        # observe its real response headers — skip with an INFO finding
        # rather than firing a false positive on a 401's headers.
        if op.requires_auth and client.default_token is None:
            return [Finding(
                rule_id=f"{self.id}-Skipped",
                severity=Severity.INFO,
                endpoint=endpoint,
                title="Header check skipped — endpoint requires auth but no token configured",
                evidence={"reason": "no_token_for_auth_required_endpoint"},
                remediation_hint=(
                    "Provide an auth token in `auth.token` to enable header "
                    "checks on this endpoint."
                ),
            )]

        # Resolve the concrete request path. OpenAPI path templates like
        # `/orders/{id}` need a value substituted; we use "1" as a
        # benign placeholder. This is sufficient because the rule only
        # reads RESPONSE headers, which are set by the server framework
        # regardless of route resolution.
        request_path = _resolve_path(op.path)

        try:
            resp = client.request("GET", request_path)
        except Exception as e:
            return [Finding(
                rule_id=f"{self.id}-ProbeFailed",
                severity=Severity.INFO,
                endpoint=endpoint,
                title="Header check failed — request error",
                evidence={"error": f"{type(e).__name__}: {e}"},
                remediation_hint="Verify the endpoint is reachable and the spec base_url is correct.",
            )]

        findings: list[Finding] = []

        # 1) Missing required headers.
        present_keys = {k.lower() for k in resp.headers.keys()}
        for h in REQUIRED_HEADERS:
            if h.lower() not in present_keys:
                findings.append(Finding(
                    rule_id=f"{self.id}-MissingHeader",
                    severity=Severity.MEDIUM,
                    endpoint=endpoint,
                    title=f"Missing security header: {h}",
                    evidence={
                        "missing_header": h,
                        "status_code": resp.status_code,
                        "present_headers": sorted(resp.headers.keys()),
                    },
                    remediation_hint=_remediation_for(h),
                ))

        # 2) Dangerous CORS combo: wildcard origin + credentials.
        aco = resp.headers.get("Access-Control-Allow-Origin", "")
        acc = resp.headers.get("Access-Control-Allow-Credentials", "")
        if aco == "*" and acc.lower() == "true":
            findings.append(Finding(
                rule_id=f"{self.id}-CORSWildcardWithCredentials",
                severity=Severity.HIGH,
                endpoint=endpoint,
                title="Wildcard CORS origin combined with allow-credentials",
                evidence={
                    "Access-Control-Allow-Origin": aco,
                    "Access-Control-Allow-Credentials": acc,
                    "status_code": resp.status_code,
                },
                remediation_hint=(
                    "Either restrict `Access-Control-Allow-Origin` to a "
                    "specific allowlist or remove `Access-Control-Allow-"
                    "Credentials: true`. Browsers actually IGNORE the "
                    "combo, but proxies/CDNs may not."
                ),
            ))
        elif aco == "*":
            findings.append(Finding(
                rule_id=f"{self.id}-CORSWildcardOrigin",
                severity=Severity.LOW,
                endpoint=endpoint,
                title="Wildcard CORS origin (no credentials)",
                evidence={
                    "Access-Control-Allow-Origin": aco,
                    "status_code": resp.status_code,
                },
                remediation_hint=(
                    "Restrict `Access-Control-Allow-Origin` to a specific "
                    "allowlist unless this endpoint is intentionally public."
                ),
            ))

        return findings


def _resolve_path(path: str) -> str:
    """Replace OpenAPI path parameters with benign placeholders.

    Examples:
      /orders/{id}             → /orders/1
      /users/{user_id}/orders  → /users/1/orders

    Returns the path unchanged when no `{...}` is present.
    """
    out = []
    i = 0
    while i < len(path):
        if path[i] == "{":
            j = path.find("}", i)
            if j == -1:
                out.append(path[i:])
                break
            out.append("1")
            i = j + 1
        else:
            out.append(path[i])
            i += 1
    return "".join(out)


def _remediation_for(header: str) -> str:
    return {
        "Strict-Transport-Security": "Set `Strict-Transport-Security: max-age=31536000; includeSubDomains` on HTTPS responses.",
        "Content-Security-Policy": "Set a `Content-Security-Policy` header restricting allowed sources. Start with `default-src 'self'`.",
        "X-Content-Type-Options": "Set `X-Content-Type-Options: nosniff` to prevent MIME-type sniffing.",
        "X-Frame-Options": "Set `X-Frame-Options: DENY` (or `SAMEORIGIN`) to prevent clickjacking.",
    }.get(header, f"Set the `{header}` header per OWASP guidance.")


# Singleton instance for easy import + registration.
rule = HeadersMisconfigRule()
