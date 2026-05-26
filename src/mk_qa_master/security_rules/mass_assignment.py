"""OWASP API3 — Broken Object Property Authorization (Mass Assignment).

PR-5 of the v0.8.0 rollout. The last rule before the MCP tool +
release PR.

What this rule does
-------------------

For each POST / PUT / PATCH operation with a JSON request body
schema, the rule:

  1. Synthesizes a **minimal valid** request body from the spec
     (required fields populated with type-appropriate placeholders;
     non-required fields skipped).
  2. Submits the body with a single **dangerous extra field**
     appended — `role: "admin"`, `is_admin: true`, etc.
  3. Inspects the response:
     - 4xx (server rejected): **SAFE**.
     - 2xx with the dangerous field echoed at the same value
       submitted: **HIGH finding** — the server stored it.
     - 2xx without the dangerous field echoed (or echoed at a
       different value, e.g. server overrode `role: admin` →
       `role: user`): **SAFE-by-echo** — server appears to whitelist
       or override. Surface as INFO so an analyst can manually
       confirm.
     - 2xx with no body to inspect: **INCONCLUSIVE INFO** — a
       follow-up GET could reach a verdict but introduces a second
       request that may need additional auth context the scanner
       doesn't have. Filed for v0.8.1 (PRD §7.2).

Probes the rule sends (one per dangerous field)
----------------------------------------------

Each (operation, dangerous field) pair is one probe. Default
catalog:

  role: "admin"        # privilege escalation
  is_admin: true       # ditto
  isAdmin: true        # JS-style naming
  admin: true          # bare flag
  is_verified: true    # bypass identity verification
  isVerified: true
  verified: true
  email_verified: true
  is_active: true      # revive banned accounts
  permissions: ["*"]   # scope escalation

Why this rule is opt-in (PRD §8)
--------------------------------

`mass_assignment` mutates server state — every probe is a POST/PUT.
On a fixture that's fine; against a real API the runner needs the
user's explicit consent. PR-6's MCP tool excludes this rule from
the default `categories` list; users opt in per scan.

What's deliberately NOT here
----------------------------

- **Follow-up GET to confirm persistence.** Echoes are a strong
  signal but not definitive. Some servers echo but don't persist;
  others persist but don't echo. A real GET on the created resource
  is the only confirmation, and it requires knowing the resource's
  URL (Location header, or `{id}` from the response). Filed for
  v0.8.1 once we have URL-extraction conventions.

- **Recursive nested-object dangerous-field substitution.** Bodies
  with nested objects (e.g. `{"user": {"role": "..."}}`) need the
  field placed at the right depth. PR-5 only probes top-level
  fields; nested filed for v0.8.1.

- **Per-spec dangerous-field discovery.** A more thorough scanner
  would parse the response schema's `properties` and probe each
  field not in the request body's `required` list. PR-5 uses a
  fixed catalog because the dangerous-field set is universal enough
  to start, and per-spec discovery is easier to bolt on once we have
  ground-truth findings to compare against.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .base import APIClient, Finding, OperationContext, Severity


# Top-level fields most APIs SHOULD reject (or silently drop) when
# a client supplies them. Tuple of (field_name, dangerous_value).
# Value chosen to be contrarian — a normal signup user is "user",
# not "admin"; "is_admin" defaults to False, etc.
DEFAULT_DANGEROUS_FIELDS: list[tuple[str, Any]] = [
    ("role", "admin"),
    ("is_admin", True),
    ("isAdmin", True),
    ("admin", True),
    ("is_verified", True),
    ("isVerified", True),
    ("verified", True),
    ("email_verified", True),
    ("is_active", True),
    ("permissions", ["*"]),
]


def _minimal_body_from_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Generate a minimal-valid JSON body for the given JSON Schema.

    Walks `properties` + `required`; for each required field, emits a
    type-appropriate placeholder. Non-required fields are skipped.
    Nested objects are recursed one level; arrays get an empty list
    unless `minItems` says otherwise (rare).

    The point is to produce a body the server will ACCEPT minus the
    dangerous extra — anything that fails the rule's probe with a
    "missing required field" 400 is a false negative we can avoid.
    """
    if not isinstance(schema, dict):
        return {}
    props: dict[str, Any] = schema.get("properties", {}) or {}
    required: list[str] = schema.get("required", []) or []
    out: dict[str, Any] = {}
    for name in required:
        spec = props.get(name, {}) or {}
        out[name] = _placeholder_for(spec, name)
    return out


def _placeholder_for(prop_schema: dict[str, Any], field_name: str) -> Any:
    """Type-based placeholder. `field_name` only used to bias strings
    toward something plausible (email-shaped if the name says
    `email`, etc.)."""
    if "enum" in prop_schema and prop_schema["enum"]:
        return prop_schema["enum"][0]
    t = prop_schema.get("type", "string")
    if t == "string":
        fmt = prop_schema.get("format", "")
        if fmt == "email" or "email" in field_name.lower():
            return "scanner@test.local"
        return "scan-test"
    if t == "integer":
        return prop_schema.get("minimum", 1)
    if t == "number":
        return float(prop_schema.get("minimum", 1.0))
    if t == "boolean":
        return False
    if t == "array":
        return []
    if t == "object":
        return _minimal_body_from_schema(prop_schema)
    return None


def _extract_json_schema(op: OperationContext) -> dict[str, Any] | None:
    """Find the application/json request body schema in an OpenAPI op.

    Returns the schema dict, or None if the operation has no JSON
    body declared.
    """
    body = (op.spec or {}).get("requestBody") or {}
    content = body.get("content") or {}
    media = content.get("application/json") or {}
    schema = media.get("schema")
    return schema if isinstance(schema, dict) else None


def _truncate(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[:n] + f"... ({len(s)} total bytes)"


def _parse_json_body(resp) -> dict[str, Any] | None:
    """Best-effort JSON parse of a response. Returns None on failure
    (non-JSON body, parse error, or top-level non-dict)."""
    try:
        data = resp.json()
    except Exception:
        return None
    return data if isinstance(data, dict) else None


@dataclass(frozen=True)
class _ProbeOutcome:
    """Tracks one (op, field) probe — used in tests to assert which
    direction the heuristic took."""
    field_name: str
    sent_value: Any
    response_status: int
    response_dict: dict[str, Any] | None
    classification: str  # "vulnerable" / "safe_rejected" / "safe_overridden" / "inconclusive"


def _classify(field_name: str, sent_value: Any, resp) -> _ProbeOutcome:
    body = _parse_json_body(resp)
    if not 200 <= resp.status_code < 300:
        return _ProbeOutcome(field_name, sent_value, resp.status_code, body, "safe_rejected")

    if body is None:
        return _ProbeOutcome(field_name, sent_value, resp.status_code, body, "inconclusive")

    if field_name in body:
        if body[field_name] == sent_value:
            return _ProbeOutcome(field_name, sent_value, resp.status_code, body, "vulnerable")
        # Server echoed but overrode the value (e.g. role: admin → role: user)
        return _ProbeOutcome(field_name, sent_value, resp.status_code, body, "safe_overridden")

    # Field absent from echo — can't tell if persisted silently
    return _ProbeOutcome(field_name, sent_value, resp.status_code, body, "inconclusive")


class MassAssignmentRule:
    """OWASP API3 mass-assignment scanner.

    Conforms to `SecurityRule` Protocol. State-mutating (POST/PUT/
    PATCH); PR-6's MCP tool excludes it from defaults.
    """

    id: str = "OWASP-API3-MassAssignment"
    severity: Severity = Severity.HIGH
    requires_auth_pair: bool = False

    # Subclasses / per-scan overrides can swap this. Kept as an
    # instance attr so the singleton at the bottom is also tunable
    # by callers via `mass_assignment_rule.dangerous_fields = ...`.
    dangerous_fields: list[tuple[str, Any]] = DEFAULT_DANGEROUS_FIELDS

    def applies_to(self, op: OperationContext) -> bool:
        if op.method.upper() not in {"POST", "PUT", "PATCH"}:
            return False
        return _extract_json_schema(op) is not None

    def execute(self, client: APIClient, op: OperationContext) -> list[Finding]:
        endpoint = f"{op.method.upper()} {op.path}"
        schema = _extract_json_schema(op)
        if schema is None:
            # applies_to should have filtered this — defensive.
            return []

        base_body = _minimal_body_from_schema(schema)
        findings: list[Finding] = []

        # If the spec declares specific properties in the request body,
        # skip dangerous fields that the spec WHITELISTS — they're
        # legitimately part of the contract, not over-posting.
        declared_props = set((schema.get("properties") or {}).keys())

        for field_name, dangerous_value in self.dangerous_fields:
            if field_name in declared_props:
                continue  # spec says this field is part of the contract
            probe_body = {**base_body, field_name: dangerous_value}
            try:
                resp = client.request(
                    op.method, op.path, json_body=probe_body,
                )
            except Exception as e:
                findings.append(Finding(
                    rule_id=f"{self.id}-ProbeFailed",
                    severity=Severity.INFO,
                    endpoint=endpoint,
                    title=f"Mass-assignment probe failed for field={field_name!r}",
                    evidence={"field": field_name, "error": f"{type(e).__name__}: {e}"},
                    remediation_hint="Verify the endpoint is reachable.",
                ))
                continue

            outcome = _classify(field_name, dangerous_value, resp)
            if outcome.classification == "vulnerable":
                findings.append(Finding(
                    rule_id=f"{self.id}-FieldEchoed",
                    severity=Severity.HIGH,
                    endpoint=endpoint,
                    title=(f"Server echoed dangerous extra field {field_name!r}={dangerous_value!r} "
                           f"— mass assignment likely"),
                    evidence={
                        "field": field_name,
                        "sent_value": dangerous_value,
                        "echoed_value": outcome.response_dict.get(field_name) if outcome.response_dict else None,
                        "status_code": outcome.response_status,
                        "response_body_preview": _truncate(resp.text, 500),
                    },
                    remediation_hint=(
                        "Whitelist allowed input fields (e.g. via Pydantic / Marshmallow "
                        "schema with `additionalProperties: false`). Don't pass the raw "
                        "request body into the ORM model. Sensitive fields like `role`, "
                        "`is_admin`, `verified` must be set server-side, never accepted "
                        "from the client."
                    ),
                ))
            elif outcome.classification == "inconclusive":
                # Don't flag — but emit a low-noise INFO so analysts
                # know we couldn't reach a verdict and should follow up.
                findings.append(Finding(
                    rule_id=f"{self.id}-Inconclusive",
                    severity=Severity.INFO,
                    endpoint=endpoint,
                    title=(f"Server accepted extra field {field_name!r} but echo didn't include it — "
                           f"manual follow-up recommended"),
                    evidence={
                        "field": field_name,
                        "sent_value": dangerous_value,
                        "status_code": outcome.response_status,
                        "response_body_preview": _truncate(resp.text, 500),
                    },
                    remediation_hint=(
                        "Manually GET the created resource and confirm whether the "
                        "dangerous field was persisted. A future scanner version "
                        "will automate this follow-up."
                    ),
                ))
            # safe_rejected (4xx) and safe_overridden (echo with
            # different value) produce NO finding — they're the
            # expected behavior of a well-behaved server.

        return findings


# Singleton for registration.
rule = MassAssignmentRule()
