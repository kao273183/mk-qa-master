"""v0.8.0 API security rules.

See `docs/prd-v0.8-api-security.md` for scope, architecture, and the
PR rollout. Each rule conforms to the `SecurityRule` Protocol in
`base.py`. The runner (PR-6) walks `ALL_RULES` and dispatches per
operation.
"""
from .base import (
    APIClient,
    AuthPair,
    Finding,
    OperationContext,
    SecurityRule,
    Severity,
)
from .bola import (
    BOLARule,
    FunctionAuthzRule,
    bola_rule,
    function_authz_rule,
)
from .broken_auth import BrokenAuthRule, rule as broken_auth_rule
from .headers_misconfig import HeadersMisconfigRule, rule as headers_misconfig_rule

# Registry of rules implemented so far. Ordered for deterministic
# scanner output. PR-5 will extend this with mass_assignment.
ALL_RULES: list[SecurityRule] = [
    headers_misconfig_rule,
    broken_auth_rule,
    bola_rule,
    function_authz_rule,
]

__all__ = [
    "ALL_RULES",
    "APIClient",
    "AuthPair",
    "BOLARule",
    "BrokenAuthRule",
    "Finding",
    "FunctionAuthzRule",
    "HeadersMisconfigRule",
    "OperationContext",
    "SecurityRule",
    "Severity",
    "bola_rule",
    "broken_auth_rule",
    "function_authz_rule",
    "headers_misconfig_rule",
]
