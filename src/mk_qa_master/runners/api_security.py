"""API security scan runner — v0.8.0.

Sibling to `schemathesis.py` (correctness fuzz) and `newman.py`
(collection replay). This one runs OWASP API Top 10 rule-based
scans against an OpenAPI spec.

The runner is a thin orchestrator. The interesting code lives in
`mk_qa_master.security_rules.*` — this module just:

  1. Loads the OpenAPI spec (URL or file://)
  2. Walks paths × methods, building OperationContext entries
  3. Filters rules by `categories` (short names) and severity
  4. Dispatches to each rule's `applies_to` + `execute`
  5. Returns the aggregate report in the v0.8 `security` block
     schema documented below.

Consent and authorization
-------------------------

The runner refuses to scan unless:

  - `QA_API_SECURITY_CONSENT=true` is set in the environment.
  - The target host (from `base_url` or the spec's `servers[0].url`)
    is in `QA_API_SECURITY_AUTHORIZED_DOMAINS` (comma-separated).

Localhost / 127.0.0.1 are implicitly authorized (Tier 1 always
works without configuration).

The `mass_assignment` rule mutates server state, so it is **excluded
from the default `categories`** list. Callers must opt in
explicitly: `categories=[..., "mass_assignment"]`.

`bola` is technically also mutating-adjacent (it could create probe
side-effects via the test ids), but its actual probes are GETs.
We keep it in the default set.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from typing import Any

import yaml

from ..security_rules import (
    ALL_RULES,
    APIClient,
    AuthPair,
    Finding,
    OperationContext,
    SecurityRule,
    Severity,
    bola_rule,
    broken_auth_rule,
    function_authz_rule,
    headers_misconfig_rule,
    mass_assignment_rule,
)


# Short-name → rule object. Keep this stable; users pass these in via
# the `categories` list arg.
RULE_BY_CATEGORY: dict[str, SecurityRule] = {
    "headers": headers_misconfig_rule,
    "broken_auth": broken_auth_rule,
    "bola": bola_rule,
    "function_authz": function_authz_rule,
    "mass_assignment": mass_assignment_rule,
}

# Default categories — everything EXCEPT mass_assignment. The latter
# mutates server state and must be opted in per PRD §8.
DEFAULT_CATEGORIES: list[str] = [
    "headers", "broken_auth", "bola", "function_authz",
]

# Hosts that are always authorized without env-var allowlist entry.
_IMPLICIT_AUTHORIZED_HOSTS = {"localhost", "127.0.0.1", "::1"}


# ---- consent / authorization --------------------------------------------

def _consent_granted() -> bool:
    return (os.environ.get("QA_API_SECURITY_CONSENT", "").lower()
            in {"true", "1", "yes"})


def _authorized_domains() -> set[str]:
    raw = os.environ.get("QA_API_SECURITY_AUTHORIZED_DOMAINS", "")
    return {d.strip().lower() for d in raw.split(",") if d.strip()}


def _host_authorized(base_url: str) -> bool:
    host = urllib.parse.urlparse(base_url).hostname
    if not host:
        return False
    host = host.lower()
    if host in _IMPLICIT_AUTHORIZED_HOSTS:
        return True
    return host in _authorized_domains()


# ---- OpenAPI loader -----------------------------------------------------

def load_spec(spec_url: str) -> dict[str, Any]:
    """Load an OpenAPI spec from an http(s):// URL or file:// path.

    Both JSON and YAML are accepted (heuristic on the response body
    rather than extension, so URLs without `.yaml` still work).
    """
    parsed = urllib.parse.urlparse(spec_url)
    if parsed.scheme in ("http", "https"):
        with urllib.request.urlopen(spec_url, timeout=10) as resp:
            body = resp.read().decode("utf-8")
    elif parsed.scheme == "file":
        with open(urllib.request.url2pathname(parsed.path), "r",
                  encoding="utf-8") as fh:
            body = fh.read()
    elif parsed.scheme == "":  # bare path
        with open(spec_url, "r", encoding="utf-8") as fh:
            body = fh.read()
    else:
        raise ValueError(f"Unsupported spec_url scheme: {parsed.scheme!r}")

    # Try YAML first (it's a superset of JSON), fall back to JSON for
    # specs that have stray YAML-incompatible chars.
    try:
        return yaml.safe_load(body)
    except yaml.YAMLError:
        return json.loads(body)


def _spec_base_url(spec: dict[str, Any]) -> str | None:
    servers = spec.get("servers") or []
    if servers and isinstance(servers[0], dict):
        return servers[0].get("url")
    return None


def _walk_operations(spec: dict[str, Any]) -> list[OperationContext]:
    """Walk the spec, return one OperationContext per (path, method)."""
    paths = spec.get("paths") or {}
    out: list[OperationContext] = []
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, op in path_item.items():
            if method.upper() not in {"GET", "POST", "PUT", "PATCH",
                                      "DELETE", "HEAD", "OPTIONS"}:
                continue
            if not isinstance(op, dict):
                continue
            requires_auth = bool(op.get("security"))
            # Resolve $ref in the requestBody schema so rules don't
            # need to walk components themselves.
            op_resolved = _resolve_refs(op, spec)
            out.append(OperationContext(
                method=method.upper(),
                path=path,
                operation_id=op_resolved.get("operationId"),
                requires_auth=requires_auth,
                spec=op_resolved,
            ))
    return out


_REF_RE = re.compile(r"^#/(.+)$")


def _resolve_refs(node: Any, root: dict[str, Any], depth: int = 0) -> Any:
    """Inline-resolve $ref pointers in a spec node.

    Doesn't try to be a full JSON Schema resolver — just handles the
    common `$ref: "#/components/schemas/Foo"` case the rules need to
    see the request body shape. Depth-limited to avoid pathological
    self-referential specs.
    """
    if depth > 12:
        return node
    if isinstance(node, dict):
        if "$ref" in node and isinstance(node["$ref"], str):
            m = _REF_RE.match(node["$ref"])
            if m:
                target = root
                for seg in m.group(1).split("/"):
                    if isinstance(target, dict) and seg in target:
                        target = target[seg]
                    else:
                        return node  # bad ref — leave it
                return _resolve_refs(target, root, depth + 1)
        return {k: _resolve_refs(v, root, depth + 1) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve_refs(v, root, depth + 1) for v in node]
    return node


# ---- result shape -------------------------------------------------------

def _summarize(findings: list[Finding]) -> dict[str, Any]:
    by_sev: dict[str, int] = {s.value: 0 for s in Severity}
    for f in findings:
        by_sev[f.severity.value] += 1
    return {
        "total": len(findings),
        "by_severity": by_sev,
    }


# ---- main entry ---------------------------------------------------------

def run_scan(
    spec_url: str,
    *,
    auth: dict[str, Any] | None = None,
    categories: list[str] | None = None,
    severity_threshold: str = "medium",
    base_url: str | None = None,
    timeout_s: int = 30,
    plan_id: str | None = None,
) -> dict[str, Any]:
    """Orchestrate an API security scan.

    Returns the v0.8 `security` block:

        {
          "scan_id": "<short uuid>",
          "spec_url": "...",
          "base_url": "...",
          "categories_run": [...],
          "rules_ran": [...],
          "findings": [Finding.to_dict() ...],
          "summary": {"total": N, "by_severity": {...}},
          "skipped": [{"reason": "...", ...}]
        }

    Or an error envelope:
        {"error": "consent_required" | "unauthorized_domain" | ...,
         "hint": "..."}

    v0.9.4 — Plan integration

    Pass `plan_id` (returned by `qa_plan`) and the scan auto-verifies
    the **findings above severity_threshold** against the plan's
    critical points. The response gains a `plan_verification` field
    with the per-CP checklist + overall status. Lets a host LLM run
    the prelude (qa_plan) → scan → verification in a single round
    trip instead of three.

    Caveat: findings below `severity_threshold` are NOT seen by the
    verifier. A CP whose verification_hint matches a LOW-severity
    finding will look unmet when the default threshold ('medium') is
    in effect. Lower the threshold (or skip it via 'info') when
    plan CPs intentionally target low-severity findings.
    """
    # ---- consent gate ----------------------------------------------------
    if not _consent_granted():
        return {
            "error": "consent_required",
            "retryable": False,
            "hint": (
                "API security scanning requires explicit consent. Set "
                "QA_API_SECURITY_CONSENT=true in the environment. See "
                "docs/prd-v0.8-api-security.md §8 for the authorization "
                "model."
            ),
            "consent_env": "QA_API_SECURITY_CONSENT",
        }

    # ---- spec load -------------------------------------------------------
    try:
        spec = load_spec(spec_url)
    except Exception as e:
        return {
            "error": "spec_load_failed",
            "retryable": False,
            "hint": f"{type(e).__name__}: {e}",
        }

    resolved_base = base_url or _spec_base_url(spec)
    if not resolved_base:
        return {
            "error": "no_base_url",
            "retryable": False,
            "hint": (
                "Spec has no `servers[0].url` and no explicit `base_url` "
                "was passed. Provide one or fix the spec."
            ),
        }

    # ---- authorization gate ----------------------------------------------
    if not _host_authorized(resolved_base):
        return {
            "error": "unauthorized_domain",
            "retryable": False,
            "hint": (
                f"Host extracted from base_url ({resolved_base!r}) is not "
                "in QA_API_SECURITY_AUTHORIZED_DOMAINS. Add it (comma-"
                "separated) to scan. Localhost is implicitly authorized."
            ),
            "host": urllib.parse.urlparse(resolved_base).hostname,
        }

    # ---- assemble client + auth pair ------------------------------------
    auth = auth or {}
    primary_token = auth.get("token")
    alt_token = auth.get("alt_user_token")
    auth_pair: AuthPair | None = None
    if primary_token and alt_token:
        auth_pair = AuthPair(
            user_a_token=primary_token,
            user_b_token=alt_token,
            bola_test_ids=auth.get("bola_test_ids"),
            fla_admin_paths=auth.get("fla_admin_paths"),
            fla_low_priv_user=auth.get("fla_low_priv_user", "user_a"),
        )

    client = APIClient(
        base_url=resolved_base,
        timeout_s=float(timeout_s),
        default_token=primary_token,
        auth_pair=auth_pair,
    )

    # ---- pick rules ------------------------------------------------------
    selected_categories = list(categories) if categories else list(DEFAULT_CATEGORIES)
    unknown = [c for c in selected_categories if c not in RULE_BY_CATEGORY]
    if unknown:
        return {
            "error": "unknown_categories",
            "retryable": False,
            "hint": (
                f"Unknown rule category names: {unknown}. Available: "
                f"{sorted(RULE_BY_CATEGORY.keys())}."
            ),
        }
    rules: list[SecurityRule] = [RULE_BY_CATEGORY[c] for c in selected_categories]

    # ---- threshold parsing ---------------------------------------------
    try:
        threshold = Severity(severity_threshold)
    except ValueError:
        return {
            "error": "bad_severity_threshold",
            "retryable": False,
            "hint": (
                f"severity_threshold must be one of "
                f"{[s.value for s in Severity]}, got {severity_threshold!r}"
            ),
        }

    # ---- run -------------------------------------------------------------
    ops = _walk_operations(spec)
    all_findings: list[Finding] = []
    for op in ops:
        for r in rules:
            if not r.applies_to(op):
                continue
            try:
                for f in r.execute(client, op):
                    all_findings.append(f)
            except Exception as e:
                # A rule blew up — surface it as INFO, keep scanning.
                all_findings.append(Finding(
                    rule_id=f"{r.id}-RunnerCaughtException",
                    severity=Severity.INFO,
                    endpoint=f"{op.method} {op.path}",
                    title=f"Rule raised: {type(e).__name__}",
                    evidence={"error": str(e), "rule": r.id},
                ))

    # ---- filter by threshold --------------------------------------------
    above_threshold = [f for f in all_findings if f.severity.meets(threshold)]

    # Sort by severity (most severe first) then endpoint for stable output.
    above_threshold.sort(key=lambda f: (f.severity.rank, f.endpoint))

    import uuid
    finding_dicts = [f.to_dict() for f in above_threshold]
    result: dict[str, Any] = {
        "scan_id": uuid.uuid4().hex[:12],
        "spec_url": spec_url,
        "base_url": resolved_base,
        "categories_run": selected_categories,
        "rules_ran": [r.id for r in rules],
        "ops_scanned": len(ops),
        "severity_threshold": threshold.value,
        "findings": finding_dicts,
        "summary": _summarize(above_threshold),
        "findings_below_threshold_count": len(all_findings) - len(above_threshold),
    }

    # v0.9.4 — optional plan auto-verification.
    if plan_id:
        # Local import: avoids circular dep if anyone ever extends
        # qa_plan to call into the runner (it doesn't today, but
        # belt-and-suspenders).
        from ..tools.qa_plan import verify_plan_tool

        verify_result = verify_plan_tool({
            "plan_id": plan_id,
            "evidence": finding_dicts,
        })
        # If verify_plan errored (e.g. plan_not_found), surface its
        # envelope under `plan_verification` rather than masking it
        # or failing the whole scan. The scan ran fine; only the
        # verification step had a problem.
        result["plan_verification"] = verify_result

    return result
