"""v0.8.0 API security rules.

See `docs/prd-v0.8-api-security.md` for scope, architecture, and the
PR rollout. Each rule conforms to the `SecurityRule` Protocol in
`base.py`. The runner (PR-6) walks `ALL_RULES` and dispatches per
operation.
"""
from .base import (
    APIClient,
    Finding,
    OperationContext,
    SecurityRule,
    Severity,
)
from .broken_auth import BrokenAuthRule, rule as broken_auth_rule
from .headers_misconfig import HeadersMisconfigRule, rule as headers_misconfig_rule

# Registry of rules implemented so far. Ordered for deterministic
# scanner output. PRs 4-5 will extend this list.
ALL_RULES: list[SecurityRule] = [
    headers_misconfig_rule,
    broken_auth_rule,
]

__all__ = [
    "ALL_RULES",
    "APIClient",
    "BrokenAuthRule",
    "Finding",
    "HeadersMisconfigRule",
    "OperationContext",
    "SecurityRule",
    "Severity",
    "broken_auth_rule",
    "headers_misconfig_rule",
]
