"""v0.9.1 — qa_plan + verify_plan tools.

Adapts the Webwright `plan.md` + critical-points pattern into a
first-class MCP tool surface. The host LLM declares what success looks
like BEFORE acting; the runner stores the checklist, hands back a
`plan_id`; later, the host (or the user) calls `verify_plan` with
evidence and gets a structured pass/fail per critical point.

Design decisions
----------------

- **In-memory store, 30-min TTL, LRU-bounded.** Same pattern as the
  v0.7 visual-challenge cache. Plans are session-scoped; they aren't
  meant to survive process restarts. If you want persistence, dump
  the plan dict into `test-results/plans/<id>.json` from the host
  side.
- **Host LLM declares the critical points; we don't NL-parse the
  task.** This mirrors Webwright: the agent writes `plan.md`, the
  harness just stores it. Saves us a brittle keyword extractor and
  gives the LLM full control over CP shape.
- **Evidence is opaque.** A CP's `verification_hint` is a free-text
  string the host LLM authors. `verify_plan` matches it against an
  `evidence` list of dicts the host LLM later assembles (test result
  rows, scan findings, log lines, screenshot paths). The matching
  rule is `verification_hint` substring presence in any evidence
  item's stringified form — simple, predictable, fails loudly.
- **`status` is computed from the per-CP ticks, not from the host's
  word.** Even if the host claims "all good", verify_plan returns
  `incomplete` when CPs are unsatisfied. That's the v0.8-mobile-
  postmortem lesson applied here: surface ground truth, don't trust
  capability claims.

Tool envelopes
--------------

    qa_plan(arguments) -> {
        "plan_id": str (12 hex chars),
        "task": str,
        "kind": str,
        "critical_points": [...],
        "created_at": str,
        "expires_at": str,
    }
        or
    {"error": "no_critical_points" | "bad_kind" | ...}

    verify_plan(arguments) -> {
        "plan_id": str,
        "status": "passed" | "incomplete" | "failed",
        "checklist": [
            {"id": str, "description": str, "satisfied": bool,
             "matched_evidence": [...]}, ...
        ],
        "unmet": [list of unsatisfied CP ids],
        "summary": {"total": int, "satisfied": int, "unsatisfied": int},
        "verified_at": str,
    }
        or
    {"error": "plan_not_found" | "expired" | "no_evidence" | ...}
"""
from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable


# ---- config -------------------------------------------------------------

# 30 minutes feels right for the plan→act→verify cycle: long enough for
# a multi-step QA flow, short enough that stale plans don't pile up if
# the user forgets to verify.
_CACHE_TTL_SECONDS = 30 * 60

# LRU cap. Higher than visual_challenge's 10 because plans are cheap
# (no screenshots) and a long debugging session might accumulate them.
_CACHE_MAX = 50

# Allowed `kind` values. Free-form makes auto-discovery harder in
# verify_plan; restricting to a known enum lets the host pick the
# right reporter / dogfood path. `None` is also accepted for hosts
# that don't want to commit to a kind upfront.
_ALLOWED_KINDS: frozenset[str] = frozenset({"run", "generate", "scan", "debug", "captcha"})


# ---- types --------------------------------------------------------------

@dataclass
class _CriticalPoint:
    cp_id: str
    description: str
    verification_hint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.cp_id,
            "description": self.description,
            "verification_hint": self.verification_hint,
        }


@dataclass
class _Plan:
    plan_id: str
    task: str
    kind: str | None
    critical_points: list[_CriticalPoint]
    created_at: datetime
    expires_at: datetime


# ---- store -------------------------------------------------------------

_ACTIVE_PLANS: "OrderedDict[str, _Plan]" = OrderedDict()
_CACHE_LOCK = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _evict_expired_locked() -> None:
    now = _now()
    expired = [pid for pid, plan in _ACTIVE_PLANS.items() if plan.expires_at <= now]
    for pid in expired:
        _ACTIVE_PLANS.pop(pid, None)


def _store_plan(plan: _Plan) -> None:
    with _CACHE_LOCK:
        _evict_expired_locked()
        while len(_ACTIVE_PLANS) >= _CACHE_MAX:
            _ACTIVE_PLANS.popitem(last=False)
        _ACTIVE_PLANS[plan.plan_id] = plan


def _fetch_plan(plan_id: str) -> _Plan | None:
    with _CACHE_LOCK:
        _evict_expired_locked()
        plan = _ACTIVE_PLANS.get(plan_id)
        if plan is None:
            return None
        _ACTIVE_PLANS.move_to_end(plan_id)
        return plan


def _reset_cache_for_tests() -> None:
    """Test hook. Don't call from production code."""
    with _CACHE_LOCK:
        _ACTIVE_PLANS.clear()


# ---- validation --------------------------------------------------------

def _normalize_critical_points(raw: Any) -> tuple[list[_CriticalPoint], str | None]:
    """Coerce caller-supplied critical_points into _CriticalPoint list.

    Accepts either:
      - list[dict] with keys {id?, description, verification_hint?}
      - list[str] (treated as descriptions; id auto-assigned;
        verification_hint defaults to description)
    """
    if not isinstance(raw, list) or len(raw) == 0:
        return [], "critical_points must be a non-empty list"

    out: list[_CriticalPoint] = []
    seen_ids: set[str] = set()
    for idx, item in enumerate(raw, start=1):
        if isinstance(item, str):
            cp_id = f"CP{idx}"
            description = item.strip()
            verification_hint = description
        elif isinstance(item, dict):
            description = str(item.get("description") or "").strip()
            if not description:
                return [], f"critical_points[{idx - 1}] missing `description`"
            cp_id = str(item.get("id") or f"CP{idx}").strip()
            verification_hint = str(item.get("verification_hint") or description).strip()
        else:
            return [], (
                f"critical_points[{idx - 1}] must be str or dict, "
                f"got {type(item).__name__}"
            )
        if cp_id in seen_ids:
            return [], f"duplicate critical_point id: {cp_id!r}"
        seen_ids.add(cp_id)
        out.append(_CriticalPoint(cp_id=cp_id, description=description,
                                  verification_hint=verification_hint))
    return out, None


def _normalize_kind(raw: Any) -> tuple[str | None, str | None]:
    if raw is None or raw == "":
        return None, None
    if not isinstance(raw, str):
        return None, f"kind must be str or None, got {type(raw).__name__}"
    kind = raw.lower().strip()
    if kind not in _ALLOWED_KINDS:
        return None, (
            f"kind must be one of {sorted(_ALLOWED_KINDS)} or omitted; "
            f"got {raw!r}"
        )
    return kind, None


# ---- evidence matching -------------------------------------------------

def _stringify_evidence_item(item: Any) -> str:
    """Flatten an evidence item to a searchable string. Dicts get
    serialized; primitives stringified."""
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        # Concatenate values rather than json.dumps — avoids quote noise
        # that would break naive substring matching.
        parts: list[str] = []
        for key, value in item.items():
            parts.append(str(key))
            if isinstance(value, (str, int, float, bool)):
                parts.append(str(value))
            elif isinstance(value, dict):
                parts.append(_stringify_evidence_item(value))
            elif isinstance(value, list):
                parts.extend(_stringify_evidence_item(v) for v in value)
            else:
                parts.append(repr(value))
        return " ".join(parts)
    if isinstance(item, list):
        return " ".join(_stringify_evidence_item(v) for v in item)
    return str(item)


def _match_cp(cp: _CriticalPoint, evidence: Iterable[Any]) -> list[Any]:
    """Return the subset of `evidence` whose stringified form contains
    the CP's verification_hint (case-insensitive). Empty list = unmet.
    """
    hint = cp.verification_hint.lower()
    if not hint:
        return []
    matched: list[Any] = []
    for item in evidence:
        flat = _stringify_evidence_item(item).lower()
        if hint in flat:
            matched.append(item)
    return matched


# ---- tool entry points -------------------------------------------------

def qa_plan_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    """Store a critical-points checklist, return a plan_id.

    Args (in `arguments` dict):
      task: str — required. The natural-language goal.
      critical_points: list[dict | str] — required, non-empty.
        Each entry is either a string (used as description+hint) or
        a dict with `description` (required), optional `id` (auto-
        generated as CP1..CPn), optional `verification_hint`
        (defaults to description — pick something that will literally
        appear in the evidence you'll later pass to verify_plan).
      kind: str | None — optional. One of:
        run / generate / scan / debug / captcha. Hint for downstream
        verifiers about which evidence stream to expect.
    """
    arguments = arguments or {}
    task = str(arguments.get("task") or "").strip()
    if not task:
        return {
            "error": "no_task",
            "retryable": False,
            "hint": "qa_plan requires a `task` description (non-empty string).",
        }

    critical_points, cp_err = _normalize_critical_points(
        arguments.get("critical_points")
    )
    if cp_err is not None:
        return {
            "error": "no_critical_points" if "must be a non-empty list" in cp_err
            else "bad_critical_points",
            "retryable": False,
            "hint": cp_err,
        }

    kind, kind_err = _normalize_kind(arguments.get("kind"))
    if kind_err is not None:
        return {
            "error": "bad_kind",
            "retryable": False,
            "hint": kind_err,
        }

    now = _now()
    plan_id = uuid.uuid4().hex[:12]
    plan = _Plan(
        plan_id=plan_id,
        task=task,
        kind=kind,
        critical_points=critical_points,
        created_at=now,
        expires_at=now + timedelta(seconds=_CACHE_TTL_SECONDS),
    )
    _store_plan(plan)
    return {
        "plan_id": plan.plan_id,
        "task": plan.task,
        "kind": plan.kind,
        "critical_points": [cp.to_dict() for cp in plan.critical_points],
        "created_at": plan.created_at.isoformat(),
        "expires_at": plan.expires_at.isoformat(),
    }


def verify_plan_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    """Walk a plan's critical points and check each against evidence.

    Args:
      plan_id: str — required.
      evidence: list[dict | str] — required, possibly empty. Each item
        is searched for the CP's verification_hint. A CP is satisfied
        when at least one evidence item contains its hint (case-
        insensitive substring).

    Returns a structured checklist with per-CP satisfaction + an
    overall status:
      - "passed" : every CP satisfied
      - "incomplete" : some CPs unsatisfied
      - "failed" : no CPs satisfied (or no evidence at all)
    """
    arguments = arguments or {}
    plan_id = arguments.get("plan_id")
    if not plan_id or not isinstance(plan_id, str):
        return {
            "error": "no_plan_id",
            "retryable": False,
            "hint": "verify_plan requires a `plan_id` returned by qa_plan.",
        }

    plan = _fetch_plan(plan_id)
    if plan is None:
        return {
            "error": "plan_not_found",
            "retryable": False,
            "hint": (
                f"Unknown plan_id {plan_id!r} (expired, evicted, or "
                f"never created). Plans live {_CACHE_TTL_SECONDS // 60} "
                f"minutes; call qa_plan to start a fresh one."
            ),
        }

    evidence = arguments.get("evidence")
    if evidence is None:
        return {
            "error": "no_evidence",
            "retryable": False,
            "hint": (
                "verify_plan requires an `evidence` list (may be empty). "
                "Pass any structured payloads — test result rows, scan "
                "findings, log lines, screenshot paths — whose contents "
                "the verifier will search for each CP's verification_hint."
            ),
        }
    if not isinstance(evidence, list):
        return {
            "error": "bad_evidence",
            "retryable": False,
            "hint": f"evidence must be a list, got {type(evidence).__name__}",
        }

    checklist: list[dict[str, Any]] = []
    satisfied_count = 0
    unmet: list[str] = []
    for cp in plan.critical_points:
        matched = _match_cp(cp, evidence)
        satisfied = bool(matched)
        if satisfied:
            satisfied_count += 1
        else:
            unmet.append(cp.cp_id)
        checklist.append({
            "id": cp.cp_id,
            "description": cp.description,
            "verification_hint": cp.verification_hint,
            "satisfied": satisfied,
            "matched_evidence": matched,
        })

    total = len(plan.critical_points)
    if satisfied_count == total:
        status = "passed"
    elif satisfied_count == 0:
        status = "failed"
    else:
        status = "incomplete"

    return {
        "plan_id": plan.plan_id,
        "task": plan.task,
        "kind": plan.kind,
        "status": status,
        "checklist": checklist,
        "unmet": unmet,
        "summary": {
            "total": total,
            "satisfied": satisfied_count,
            "unsatisfied": total - satisfied_count,
        },
        "verified_at": _now().isoformat(),
    }
