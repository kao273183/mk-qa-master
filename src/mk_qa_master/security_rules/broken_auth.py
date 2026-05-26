"""OWASP API2 — Broken Authentication.

PR-3 of the v0.8.0 rollout. For each operation declared to require
auth in the OpenAPI spec, this rule sends a small set of
**intentionally-forged tokens** and watches whether the server lets
them through. A 2xx response to any of the forged probes indicates
the server isn't validating tokens properly — the OWASP API2
textbook bug class.

Tampering matrix (4 probes per auth-required op)
------------------------------------------------

| Probe              | What we send                                | Expect | Severity if 2xx |
|--------------------|---------------------------------------------|--------|------------------|
| NoAuth             | no Authorization header                     | 401/403 | CRITICAL — no auth at all
| MalformedJWT       | `"not.a.jwt.at.all"` as Bearer              | 401/403 | MEDIUM — parser accepts garbage
| AlgNone            | hand-rolled `alg: none` JWT, admin claims   | 401/403 | HIGH — algorithm not pinned
| WrongSignature     | PyJWT-encoded with a random secret          | 401/403 | HIGH — signature not verified

A 4xx other than 401/403 (e.g. 400 bad-request) is treated as
"server rejected the token but not for auth reasons" — still
considered SAFE for this rule's purposes, since the server didn't
honor the forged credentials. Only 2xx counts as a finding.

JWT library decision (PRD §7.1)
-------------------------------

Picked **PyJWT** for v0.8.0 because:
  - already a transitive dep of the Tier 1 fixture (PR-1)
  - covers everything PR-3 needs (HS256 sign, decode with options,
    explicit algorithm whitelist)
  - simpler API surface than python-jose

`alg: none` tokens are NOT encodable through PyJWT 2.x by design.
The `_forge_alg_none_jwt` helper hand-rolls one with raw
base64-url segments. Same shape as a real attacker's tool would
produce.

Why we don't test 'expired token accepted'
------------------------------------------

Producing a validly-signed-but-expired token requires the SERVER'S
signing key, which a black-box scanner doesn't have. We can't
distinguish "server rejected because signature wrong" from "server
rejected because expired" without that key. Filed as future work
for an authenticated-scan mode where the user supplies a real token
and the rule can mutate just the `exp` claim while keeping the
signature valid.
"""
from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass

import jwt as pyjwt

from .base import APIClient, Finding, OperationContext, Severity
from .headers_misconfig import _resolve_path


# Random secret for the WrongSignature probe. Hardcoded because it's
# meant to be DIFFERENT from any real server's secret — the value
# itself doesn't matter as long as it never accidentally matches.
_ATTACKER_SECRET = "attacker-key-not-real-anywhere-2026-mk-qa-master-v0.8"


def _forge_alg_none_jwt(claims: dict) -> str:
    """Hand-roll an `alg: none` JWT. PyJWT 2.x refuses to encode these.

    Format: base64url(header) "." base64url(claims) "." ""
    Trailing empty signature is what makes alg:none syntactically
    valid — anything that strictly validates structure will accept
    the parse and then either reject on alg-policy (safe) or honor
    the claims (vulnerable).
    """
    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(
            json.dumps(d, separators=(",", ":")).encode()
        ).rstrip(b"=").decode()

    header = {"alg": "none", "typ": "JWT"}
    return f"{b64(header)}.{b64(claims)}."


def _encode_wrong_sig_jwt(claims: dict) -> str:
    """Sign claims with a key we KNOW the server doesn't use."""
    return pyjwt.encode(claims, _ATTACKER_SECRET, algorithm="HS256")


@dataclass(frozen=True)
class _Probe:
    name: str               # short id, included in rule_id
    token: str | None       # None = no Authorization header
    severity_if_2xx: Severity
    description: str        # one-line, surfaces in finding title


class BrokenAuthRule:
    """OWASP API2 — Broken Authentication scanner.

    Conforms to `SecurityRule` Protocol. Single-user (no auth pair
    needed); always probes with forged tokens regardless of any
    real token the client holds.
    """

    id: str = "OWASP-API2-BrokenAuth"
    severity: Severity = Severity.HIGH
    requires_auth_pair: bool = False

    def applies_to(self, op: OperationContext) -> bool:
        # Only auth-required endpoints can demonstrate broken auth.
        # Endpoints that don't claim auth in the spec are out of scope
        # for this rule (covered by other rules if they're sensitive).
        return op.requires_auth

    def execute(self, client: APIClient, op: OperationContext) -> list[Finding]:
        endpoint = f"{op.method.upper()} {op.path}"
        path = _resolve_path(op.path)
        method = op.method.upper()

        # Build the probe matrix. `claims` carries an admin role so a
        # vulnerable server doesn't merely accept the token — it
        # accepts CLAIMS THE ATTACKER FORGED, which is the real harm.
        forged_admin_claims = {
            "sub": "999",
            "username": "attacker",
            "role": "admin",
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
        }

        probes: list[_Probe] = [
            _Probe(
                name="NoAuth",
                token=None,
                severity_if_2xx=Severity.CRITICAL,
                description="No Authorization header — server accepted unauthenticated request",
            ),
            _Probe(
                name="MalformedJWT",
                token="not.a.jwt.at.all",
                severity_if_2xx=Severity.MEDIUM,
                description="Malformed JWT — server accepted a syntactically-invalid token",
            ),
            _Probe(
                name="AlgNone",
                token=_forge_alg_none_jwt(forged_admin_claims),
                severity_if_2xx=Severity.HIGH,
                description="alg:none JWT — server accepted an unsigned token claiming admin",
            ),
            _Probe(
                name="WrongSignature",
                token=_encode_wrong_sig_jwt(forged_admin_claims),
                severity_if_2xx=Severity.HIGH,
                description="Wrong-signature JWT — server didn't validate the signature",
            ),
        ]

        findings: list[Finding] = []
        for probe in probes:
            try:
                resp = client.request(method, path, token=probe.token)
            except Exception as e:
                findings.append(Finding(
                    rule_id=f"{self.id}-ProbeFailed-{probe.name}",
                    severity=Severity.INFO,
                    endpoint=endpoint,
                    title=f"{probe.name} probe failed — request error",
                    evidence={"probe": probe.name, "error": f"{type(e).__name__}: {e}"},
                    remediation_hint="Verify the endpoint is reachable and the spec base_url is correct.",
                ))
                continue

            # Only 2xx is a finding. 401/403 = safe. Other 4xx / 5xx =
            # ambiguous (server rejected for a different reason).
            if 200 <= resp.status_code < 300:
                findings.append(Finding(
                    rule_id=f"{self.id}-{probe.name}",
                    severity=probe.severity_if_2xx,
                    endpoint=endpoint,
                    title=probe.description,
                    evidence={
                        "probe": probe.name,
                        "status_code": resp.status_code,
                        "response_body_preview": _truncate(resp.text, 500),
                    },
                    remediation_hint=_remediation_for(probe.name),
                ))

        return findings


def _truncate(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[:n] + f"... ({len(s)} total bytes)"


def _remediation_for(probe_name: str) -> str:
    return {
        "NoAuth": (
            "Require a valid Authorization header on this endpoint. "
            "Reject with 401 when missing."
        ),
        "MalformedJWT": (
            "Validate JWT structure (3 base64-encoded segments) before "
            "consuming claims. Most JWT libraries do this by default — "
            "make sure you're using `decode()` not custom parsing."
        ),
        "AlgNone": (
            "Pin the accepted algorithm list. PyJWT: `algorithms=['HS256']`, "
            "never `algorithms=['none', ...]`. Reject tokens with `alg: none`."
        ),
        "WrongSignature": (
            "Verify the JWT signature with your signing key. Do NOT pass "
            "`options={'verify_signature': False}` in production code paths."
        ),
    }.get(probe_name, "Validate the token before honoring its claims.")


# Singleton for easy registration in __init__.ALL_RULES.
rule = BrokenAuthRule()
