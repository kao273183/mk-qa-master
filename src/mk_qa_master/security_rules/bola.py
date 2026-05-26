"""OWASP API1 (BOLA / IDOR) + OWASP API5 (Function Level Authz).

PR-4 of the v0.8.0 rollout. Two rules in one module because they
share the "low-priv token sees something it shouldn't" diff
machinery — only the selector differs.

  BOLARule          — for each GET endpoint with exactly one path
                      parameter, probe with user-A's token using
                      user-B's object id. 2xx = the server returned
                      user-B's data to user-A.

  FunctionAuthzRule — for each operation whose path matches a known
                      admin-shaped pattern (default: "/admin/"),
                      probe with the LOW-PRIV token. 2xx = the
                      server granted admin access to a non-admin
                      user.

Both rules carry `requires_auth_pair = True`. When the runner
doesn't have an `AuthPair`, they skip with INFO findings rather
than firing false positives off the default-token probe.

Discovery strategy decision (PRD §7.3)
--------------------------------------

For BOLA we picked **explicit config** — `auth_pair.bola_test_ids`
maps each user to the ids of objects they own. The alternative
strategies were:

  (a) Explicit config           ← picked
  (b) Auto-seed via POST endpoints
  (c) Discover via "list me's objects" endpoints

(a) is the simplest, deterministic, and has zero side effects
during a security scan. (b)/(c) are deferred to v0.8.1 — they
involve mutating server state or assuming spec-quality patterns
that aren't universal.

Multi-parameter paths
---------------------

For PR-4 the BOLA rule applies only to paths with exactly ONE path
parameter. Paths like `/users/{user_id}/orders/{order_id}` need a
richer substitution strategy (which id swaps to user-B's, which
stays?) and are filed for v0.8.1. The rule emits an INFO finding
on multi-param paths so users know they were skipped.
"""
from __future__ import annotations

import re
from typing import Any

from .base import APIClient, AuthPair, Finding, OperationContext, Severity


_DEFAULT_FLA_ADMIN_PATHS = ["/admin/", "/admin"]


def _count_path_params(path: str) -> int:
    return len(re.findall(r"\{[^{}]+\}", path))


def _substitute_first_path_param(path: str, value: int | str) -> str:
    """Replace the first `{...}` token with `value`. Leaves the rest
    alone — used as a sanity check; BOLA only acts on single-param
    paths so the "leaves the rest alone" branch is unused for now."""
    return re.sub(r"\{[^{}]+\}", str(value), path, count=1)


def _matches_admin_pattern(path: str, patterns: list[str]) -> bool:
    return any(p in path for p in patterns)


def _truncate(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[:n] + f"... ({len(s)} total bytes)"


# ---- BOLA (OWASP API1) ----------------------------------------------------

class BOLARule:
    """Broken Object Level Authorization scanner.

    For each (auth-required GET, single path-param) operation:
      For each (user-A, user-B) ordered pair in `auth_pair`:
        probe path-with-user-B-id with user-A's token.
        if 2xx → CRITICAL finding (cross-user data exposure).
    """

    id: str = "OWASP-API1-BOLA"
    severity: Severity = Severity.HIGH  # default; per-finding CRITICAL
    requires_auth_pair: bool = True

    def applies_to(self, op: OperationContext) -> bool:
        if not op.requires_auth:
            return False
        if op.method.upper() != "GET":
            return False
        # Single path parameter only. Multi-param paths punt to v0.8.1.
        if _count_path_params(op.path) != 1:
            return False
        return True

    def execute(self, client: APIClient, op: OperationContext) -> list[Finding]:
        endpoint = f"{op.method.upper()} {op.path}"
        pair = client.auth_pair
        if pair is None:
            return [_skip_finding(self.id, endpoint, "no_auth_pair_provided",
                                   "Provide `auth.alt_user_token` to enable BOLA probing.")]
        if not pair.bola_test_ids:
            return [_skip_finding(self.id, endpoint, "no_bola_test_ids",
                                   "Provide `auth.bola_test_ids` mapping each user to ids of "
                                   "objects they own.")]

        user_a_ids = pair.bola_test_ids.get("user_a", [])
        user_b_ids = pair.bola_test_ids.get("user_b", [])
        if not user_a_ids or not user_b_ids:
            return [_skip_finding(self.id, endpoint, "incomplete_bola_test_ids",
                                   "bola_test_ids must list at least one id under both "
                                   "`user_a` and `user_b`.")]

        findings: list[Finding] = []

        # Direction 1: user_a's token tries to read user_b's first object.
        findings.extend(self._probe_direction(
            client, op, endpoint,
            actor_token=pair.user_a_token, actor_label="user_a",
            target_id=user_b_ids[0], target_owner_label="user_b",
        ))
        # Direction 2: user_b's token tries to read user_a's first object.
        findings.extend(self._probe_direction(
            client, op, endpoint,
            actor_token=pair.user_b_token, actor_label="user_b",
            target_id=user_a_ids[0], target_owner_label="user_a",
        ))
        return findings

    def _probe_direction(
        self, client: APIClient, op: OperationContext, endpoint: str,
        *, actor_token: str, actor_label: str,
        target_id: int, target_owner_label: str,
    ) -> list[Finding]:
        request_path = _substitute_first_path_param(op.path, target_id)
        try:
            resp = client.request(op.method, request_path, token=actor_token)
        except Exception as e:
            return [Finding(
                rule_id=f"{self.id}-ProbeFailed",
                severity=Severity.INFO,
                endpoint=endpoint,
                title=f"BOLA probe failed: {actor_label} → {target_owner_label}'s id={target_id}",
                evidence={"actor": actor_label, "target_id": target_id,
                          "error": f"{type(e).__name__}: {e}"},
                remediation_hint="Verify the endpoint is reachable.",
            )]

        if 200 <= resp.status_code < 300:
            return [Finding(
                rule_id=f"{self.id}-CrossUserDataExposure",
                severity=Severity.CRITICAL,
                endpoint=endpoint,
                title=(f"{actor_label} can read {target_owner_label}'s object id={target_id} — "
                       f"missing object-level authorization check"),
                evidence={
                    "actor": actor_label,
                    "target_owner": target_owner_label,
                    "target_id": target_id,
                    "probed_path": request_path,
                    "status_code": resp.status_code,
                    "response_body_preview": _truncate(resp.text, 500),
                },
                remediation_hint=(
                    "Compare the caller's identity to the object's owner before "
                    "returning. Reject with 403 (or 404 to avoid id-enumeration) "
                    "when the caller is not the owner. Don't rely on the spec's "
                    "`security` declaration alone — it tells you 'someone is "
                    "logged in,' not 'this specific user owns this object.'"
                ),
            )]
        return []


# ---- Function Level Authorization (OWASP API5) ---------------------------

class FunctionAuthzRule:
    """Broken Function Level Authorization scanner.

    For each auth-required operation whose path matches an admin
    pattern, probe with the LOW-PRIV token. 2xx = admin function
    accessible to non-admin users.
    """

    id: str = "OWASP-API5-FunctionAuthz"
    severity: Severity = Severity.HIGH
    requires_auth_pair: bool = True

    def applies_to(self, op: OperationContext) -> bool:
        if not op.requires_auth:
            return False
        # Path-pattern selection. Production scanners would also honor
        # spec-declared scopes (e.g. OAuth2 `admin` scope) but our
        # fixture and most simple specs lack them. Path-pattern is a
        # safe default; an `fla_admin_paths` override per-scan lets
        # callers tune this.
        return True  # actual filtering happens in execute() so we can
                     # surface "no auth_pair" INFO findings consistently

    def execute(self, client: APIClient, op: OperationContext) -> list[Finding]:
        endpoint = f"{op.method.upper()} {op.path}"
        pair = client.auth_pair
        if pair is None:
            # FLA only fires for admin paths. Don't emit "skipped" on
            # every non-admin endpoint; only on admin paths where we
            # would have probed.
            patterns = _DEFAULT_FLA_ADMIN_PATHS
            if not _matches_admin_pattern(op.path, patterns):
                return []
            return [_skip_finding(self.id, endpoint, "no_auth_pair_provided",
                                   "Provide `auth.alt_user_token` to enable FLA probing on "
                                   "admin-shaped paths.")]

        patterns = pair.fla_admin_paths or _DEFAULT_FLA_ADMIN_PATHS
        if not _matches_admin_pattern(op.path, patterns):
            return []  # not an admin path — nothing to probe

        # Pick the low-priv token. Default: user_a.
        low_priv_token = (pair.user_a_token if pair.fla_low_priv_user == "user_a"
                          else pair.user_b_token)
        low_priv_label = pair.fla_low_priv_user

        # Substitute any path params with "1" — same approach as
        # headers_misconfig. Function-level authz finding is about
        # WHO can call the endpoint, not what they pass to it.
        from .headers_misconfig import _resolve_path
        request_path = _resolve_path(op.path)

        try:
            resp = client.request(op.method, request_path, token=low_priv_token)
        except Exception as e:
            return [Finding(
                rule_id=f"{self.id}-ProbeFailed",
                severity=Severity.INFO,
                endpoint=endpoint,
                title="FLA probe failed — request error",
                evidence={"low_priv_user": low_priv_label,
                          "error": f"{type(e).__name__}: {e}"},
                remediation_hint="Verify the endpoint is reachable.",
            )]

        if 200 <= resp.status_code < 300:
            return [Finding(
                rule_id=f"{self.id}-NonAdminAccessGranted",
                severity=Severity.HIGH,
                endpoint=endpoint,
                title=(f"Admin-shaped endpoint accessible to {low_priv_label} "
                       f"(no role check)"),
                evidence={
                    "low_priv_user": low_priv_label,
                    "matched_pattern": next((p for p in patterns if p in op.path), ""),
                    "status_code": resp.status_code,
                    "response_body_preview": _truncate(resp.text, 500),
                },
                remediation_hint=(
                    "Check the caller's role / scope claim before honoring the "
                    "request. Reject with 403 when the role doesn't include "
                    "elevated privileges. The fact that a token is valid is "
                    "NOT sufficient authorization for admin-only endpoints."
                ),
            )]
        return []


# ---- shared helper -------------------------------------------------------

def _skip_finding(rule_id: str, endpoint: str, reason: str, hint: str) -> Finding:
    return Finding(
        rule_id=f"{rule_id}-Skipped",
        severity=Severity.INFO,
        endpoint=endpoint,
        title=f"{rule_id} skipped: {reason}",
        evidence={"reason": reason},
        remediation_hint=hint,
    )


# Singletons for easy registration.
bola_rule = BOLARule()
function_authz_rule = FunctionAuthzRule()
