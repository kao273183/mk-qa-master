"""Base types for the v0.8.0 API security rules.

The runner walks a list of rules, calls `applies_to(op)` to decide
whether a rule should run against a given OpenAPI operation, then
calls `execute(client, op)` and collects the returned `Finding`s.

PR-2 (this commit) lands the abstraction and one rule
(`headers_misconfig`). PRs 3-5 add `broken_auth`, `bola`,
`mass_assignment` against the same Protocol.

Design notes

- `SecurityRule` is a Protocol, not a base class. Rules can be either
  classes or module-level singletons — whatever fits.
- `@runtime_checkable` so the runner can do `isinstance(x, SecurityRule)`
  for diagnostics; not relied on for hot-path dispatch.
- `OperationContext` decouples rules from the raw OpenAPI shape. Adding
  a new field here doesn't force every rule to learn it; rules only
  read what they care about.
- `APIClient` is intentionally minimal — no retries, no auth refresh,
  no connection pooling. Rules that need more (e.g. mass-assignment's
  follow-up GET) compose calls themselves rather than asking the
  client to grow capabilities.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol, runtime_checkable

import requests


class Severity(str, Enum):
    """Finding severity, ordered by priority.

    String-valued so it serializes cleanly into report.json (no
    `Severity.HIGH` repr leaking through). Order matters — `>=` lets
    the runner filter by `severity_threshold`.
    """
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    @property
    def rank(self) -> int:
        # Lower number = more severe. Useful for sorting findings.
        return {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}[self.value]

    def meets(self, threshold: "Severity") -> bool:
        """True if this finding is at least as severe as the threshold."""
        return self.rank <= threshold.rank


@dataclass
class Finding:
    """One security finding produced by a rule against one endpoint.

    `evidence` carries rule-specific detail — response headers, the
    forged token used to elicit the bug, the request body that
    triggered persistence, etc. Stays a dict so we don't lock the
    shape per-rule.
    """
    rule_id: str
    severity: Severity
    endpoint: str            # human-readable, e.g. "GET /vuln/data"
    title: str               # one-line summary, shown in HTML reporter
    evidence: dict[str, Any] = field(default_factory=dict)
    remediation_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity.value,
            "endpoint": self.endpoint,
            "title": self.title,
            "evidence": self.evidence,
            "remediation_hint": self.remediation_hint,
        }


@dataclass
class OperationContext:
    """One OpenAPI operation reshaped for rule consumption.

    `spec` is the raw operation object — rules that need anything
    exotic (e.g. read `x-vulnerable` for self-test, inspect parameter
    schemas) reach into it directly. Common fields are surfaced as
    typed attributes.
    """
    method: str              # "GET" / "POST" / "PUT" / "PATCH" / "DELETE"
    path: str                # OpenAPI path template, e.g. "/orders/{id}"
    operation_id: str | None
    requires_auth: bool      # derived from the operation's `security` array
    spec: dict[str, Any] = field(default_factory=dict)


@dataclass
class AuthPair:
    """Two-user auth context for rules that need cross-user diffing.

    PR-4 introduces this for BOLA (API1) — a vulnerable endpoint
    returns user-B's object when called with user-A's token. Detecting
    that requires:
      - two real, valid tokens (we can't synthesize "user B" from
        user A's token, because the server should be cryptographically
        distinguishing them)
      - knowledge of which object ids each user owns, so the rule
        knows which forbidden id to substitute into the path

    `bola_test_ids` is the discovery-strategy decision called out in
    PRD §7.3 — we picked **explicit config** rather than auto-create
    or seed-endpoint probing. Keeps the rule deterministic and avoids
    POST/DELETE side effects during a security scan. Future PR-4.1
    can add auto-seed for users who don't want to maintain the table.

    `fla_admin_paths`: substrings that mark a path as elevated-priv.
    Function Level Authz rule probes these with the LOW-priv token
    (user_a) — a 2xx response is the API5 finding.
    """
    user_a_token: str
    user_b_token: str
    # {"user_a": [1, 3], "user_b": [2]} — ids of objects each user owns.
    # Required for the BOLA rule; FLA rule doesn't use this.
    bola_test_ids: dict[str, list[int]] | None = None
    # Substring matches against the OpenAPI path. Default below if None.
    fla_admin_paths: list[str] | None = None
    # Which user is "low-priv" for the FLA rule. Default: user_a.
    fla_low_priv_user: str = "user_a"


@dataclass
class APIClient:
    """Thin HTTP wrapper used by rules to probe endpoints.

    Holds the base URL + default timeout + optional auth token.
    Rules can override the token per-call (for tampering / fuzzing
    scenarios) without mutating the client.

    `auth_pair` is the optional two-user context introduced by PR-4
    for BOLA + FLA rules. Rules with `requires_auth_pair = True` check
    this and skip with an INFO finding when missing.
    """
    base_url: str
    timeout_s: float = 10.0
    default_token: str | None = None
    auth_pair: AuthPair | None = None

    def request(
        self,
        method: str,
        path: str,
        *,
        token: str | None | object = ...,  # sentinel = "use default"
        json_body: Any = None,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> requests.Response:
        """Make a single HTTP request and return the raw Response.

        `token` semantics:
          - omitted (default sentinel)  → use `default_token`
          - explicit `None`             → send no Authorization header
          - explicit string             → send `Bearer <token>`
        """
        url = self.base_url.rstrip("/") + path
        h = dict(headers or {})
        chosen_token = self.default_token if token is ... else token
        if chosen_token is not None:
            h["Authorization"] = f"Bearer {chosen_token}"
        return requests.request(
            method=method.upper(),
            url=url,
            json=json_body,
            params=params,
            headers=h,
            timeout=self.timeout_s,
        )


@runtime_checkable
class SecurityRule(Protocol):
    """Protocol every v0.8 security rule conforms to.

    Rules are addressed by stable string `id` so the runner can:
      - select / deselect via `categories: list[str]`
      - tag findings with origin
      - serialize the "rules that ran" set into report.json

    `severity` on the rule itself is the **default** severity for
    findings produced; individual findings may set higher / lower per
    case (e.g. wildcard-CORS-with-credentials is HIGH even though the
    headers rule defaults to MEDIUM).

    `requires_auth_pair` is True only for rules that need TWO user
    tokens (alice + bob) — currently just BOLA. Rules that need ONE
    token use `client.default_token` directly.
    """
    id: str
    severity: Severity
    requires_auth_pair: bool

    def applies_to(self, op: OperationContext) -> bool: ...
    def execute(self, client: APIClient, op: OperationContext) -> list[Finding]: ...
