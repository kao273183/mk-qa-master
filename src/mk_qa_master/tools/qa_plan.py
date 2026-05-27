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

import json
import os
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
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


def _store_plan(plan: _Plan) -> Path | None:
    """Store a plan in-memory and optionally to disk.

    Returns the persisted file path when disk persistence ran successfully,
    None otherwise. Callers can surface this in the tool response so the
    host LLM knows where to find the plan after a restart.
    """
    with _CACHE_LOCK:
        _evict_expired_locked()
        while len(_ACTIVE_PLANS) >= _CACHE_MAX:
            _ACTIVE_PLANS.popitem(last=False)
        _ACTIVE_PLANS[plan.plan_id] = plan
    if _persistence_enabled():
        return _persist_plan(plan)
    return None


def _fetch_plan(plan_id: str) -> tuple["_Plan | None", str]:
    """Fetch a plan, returning (plan, source).

    source is "memory" (cache hit), "disk" (fell through to persisted
    file), or "none" (not found anywhere). Returned alongside the plan
    so verify_plan can surface where the data came from in its
    response.
    """
    with _CACHE_LOCK:
        _evict_expired_locked()
        plan = _ACTIVE_PLANS.get(plan_id)
        if plan is not None:
            _ACTIVE_PLANS.move_to_end(plan_id)
            return plan, "memory"
    if _persistence_enabled():
        disk_plan = _load_persisted_plan(plan_id)
        if disk_plan is not None:
            # Repopulate the in-memory cache so subsequent verify_plan
            # calls in the same process hit memory instead of disk.
            with _CACHE_LOCK:
                while len(_ACTIVE_PLANS) >= _CACHE_MAX:
                    _ACTIVE_PLANS.popitem(last=False)
                _ACTIVE_PLANS[plan_id] = disk_plan
            return disk_plan, "disk"
    return None, "none"


def _reset_cache_for_tests() -> None:
    """Test hook. Don't call from production code.

    Only clears the in-memory cache. Tests that exercise persistence
    should use tmp_path + monkeypatched MK_QA_PLANS_DIR so the disk
    state is per-test isolated automatically.
    """
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


# ---- persistence (v0.9.3) ---------------------------------------------

# v0.9.1 plans lived only in-memory (30-min TTL, LRU cap 50). That
# meant: process restart → all plans gone; long-running QA flow that
# crosses TTL → plan_id silently invalid. v0.9.3 adds an opt-in disk
# layer: when enabled, every qa_plan write also atomically dumps the
# plan to `<plans_dir>/<plan_id>.json`, and verify_plan transparently
# falls back to disk on in-memory misses.
#
# Default policy
# --------------
#   - Enabled when QA_PROJECT_ROOT is set (mk-qa-master is "configured").
#   - Disabled when QA_PROJECT_ROOT is unset (ad-hoc invocations
#     shouldn't surprise users with file writes).
#   - QA_PLAN_PERSIST=true|false explicitly overrides either way.
#
# Path resolution
# ---------------
#   1. MK_QA_PLANS_DIR env override
#   2. <QA_PROJECT_ROOT>/test-results/plans/
#   3. ./test-results/plans/ (CWD fallback for opt-in-without-root)


def _persistence_enabled() -> bool:
    """Decide whether to write/read plan files this call.

    Lazy (env-evaluated each call) so tests can monkeypatch cleanly.
    """
    explicit = os.environ.get("QA_PLAN_PERSIST", "").strip().lower()
    if explicit in {"true", "1", "yes", "on"}:
        return True
    if explicit in {"false", "0", "no", "off"}:
        return False
    # Default policy: ON when QA_PROJECT_ROOT is set, else OFF.
    return bool(os.environ.get("QA_PROJECT_ROOT", "").strip())


def _plans_dir() -> Path:
    """Locate the dir for persisted plan files.

    Order: MK_QA_PLANS_DIR → <QA_PROJECT_ROOT>/test-results/plans → ./test-results/plans
    """
    override = os.environ.get("MK_QA_PLANS_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    project_root = os.environ.get("QA_PROJECT_ROOT", "").strip()
    base = Path(project_root).expanduser() if project_root else Path.cwd()
    return (base / "test-results" / "plans").resolve()


def _plan_to_json(plan: "_Plan") -> dict[str, Any]:
    return {
        "plan_id": plan.plan_id,
        "task": plan.task,
        "kind": plan.kind,
        "critical_points": [cp.to_dict() for cp in plan.critical_points],
        "created_at": plan.created_at.isoformat(),
        "expires_at": plan.expires_at.isoformat(),
        "_schema": "mk-qa-master.plan.v1",
    }


def _plan_from_json(data: dict[str, Any]) -> "_Plan | None":
    """Best-effort reverse of _plan_to_json. Returns None on shape mismatch."""
    try:
        plan_id = data["plan_id"]
        task = data["task"]
        kind = data.get("kind")
        cps_raw = data["critical_points"]
        created_at = datetime.fromisoformat(data["created_at"])
        expires_at = datetime.fromisoformat(data["expires_at"])
    except (KeyError, ValueError, TypeError):
        return None
    if not isinstance(cps_raw, list):
        return None
    cps: list[_CriticalPoint] = []
    for entry in cps_raw:
        if not isinstance(entry, dict):
            return None
        try:
            cps.append(_CriticalPoint(
                cp_id=str(entry["id"]),
                description=str(entry["description"]),
                verification_hint=str(entry.get("verification_hint")
                                      or entry["description"]),
            ))
        except KeyError:
            return None
    return _Plan(
        plan_id=plan_id, task=task, kind=kind,
        critical_points=cps,
        created_at=created_at, expires_at=expires_at,
    )


def _persist_plan(plan: "_Plan") -> Path | None:
    """Atomically write the plan to <plans_dir>/<plan_id>.json.

    Best-effort: any OSError is swallowed (returns None) so a read-only
    filesystem or permission issue never breaks the qa_plan tool.
    Uses tempfile + os.replace for atomicity — readers never see a
    partial file.
    """
    try:
        plans_dir = _plans_dir()
        plans_dir.mkdir(parents=True, exist_ok=True)
        target = plans_dir / f"{plan.plan_id}.json"
        # Write to a sibling temp file then atomic-rename. tempfile.NamedTemporaryFile
        # avoids name collisions across concurrent writers.
        import tempfile
        fd, tmp_path = tempfile.mkstemp(
            prefix=f"{plan.plan_id}.", suffix=".tmp", dir=str(plans_dir)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(_plan_to_json(plan), fh, indent=2)
            os.replace(tmp_path, target)
        except OSError:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            return None
        return target
    except OSError:
        return None


def _load_persisted_plan(plan_id: str) -> "_Plan | None":
    """Read a plan back from disk. Returns None on missing / malformed.

    Expiry semantics: we honor the original `expires_at` even on disk.
    A plan that was created 2 hours ago with a 30-min TTL is "expired"
    even if its file still exists; load returns None. This matches the
    in-memory TTL behavior and prevents stale plans from being
    silently re-instated.

    For "I want the plan to survive longer than the in-memory TTL",
    the right knob is a longer TTL at creation time (future v0.9.x
    arg), not bypassing expiry checks on read.
    """
    try:
        plans_dir = _plans_dir()
        target = plans_dir / f"{plan_id}.json"
        if not target.is_file():
            return None
        text = target.read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    plan = _plan_from_json(data)
    if plan is None:
        return None
    if plan.expires_at <= _now():
        return None  # honor expiry even when file still exists
    return plan


# ---- auto-discovery (v0.9.2) ------------------------------------------

def _default_report_path() -> Path:
    """Locate the project's pytest-json-report file.

    Order:
      1. `MK_QA_REPORT_PATH` env override (absolute path)
      2. `<QA_PROJECT_ROOT>/report.json` (mk-qa-master default — see
         `mk_qa_master.config.REPORT_PATH`)
      3. `./report.json` (CWD fallback for ad-hoc invocations)

    We resolve at call time, not import time, so tests can monkeypatch
    the env without re-importing.
    """
    override = os.environ.get("MK_QA_REPORT_PATH", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    project_root = os.environ.get("QA_PROJECT_ROOT", "").strip()
    if project_root:
        return (Path(project_root).expanduser() / "report.json").resolve()
    return (Path.cwd() / "report.json").resolve()


def _autodiscover_evidence(report_path: Path | None) -> tuple[list[Any], str | None]:
    """Read a pytest-json-report file and turn its `tests` list into
    evidence items. Returns (rows, source_path_str_for_debug).

    On any read / parse failure we return ([], None) — auto-discovery
    is best-effort, not load-bearing. Callers that want a hard error
    when the report is missing should pass evidence explicitly.

    The pytest-json-report shape is:
        {"summary": {...}, "tests": [
            {"nodeid": "tests/test_login.py::test_valid",
             "outcome": "passed", "duration": 1.2, ...},
            ...
        ]}

    We surface each test row as-is — `nodeid` carries the test name
    that CPs typically reference; `outcome` lets a CP's hint say
    "passed" / "failed" to be outcome-conditional. The runner-specific
    extras (call info, capture, etc.) come along but are ignored by
    the matcher.
    """
    if report_path is None:
        return [], None
    try:
        if not report_path.is_file():
            return [], None
        text = report_path.read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, json.JSONDecodeError):
        return [], None
    if not isinstance(data, dict):
        return [], None
    tests = data.get("tests")
    if not isinstance(tests, list):
        return [], None
    return tests, str(report_path)


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
    persisted_to = _store_plan(plan)
    return {
        "plan_id": plan.plan_id,
        "task": plan.task,
        "kind": plan.kind,
        "critical_points": [cp.to_dict() for cp in plan.critical_points],
        "created_at": plan.created_at.isoformat(),
        "expires_at": plan.expires_at.isoformat(),
        "persisted_to": str(persisted_to) if persisted_to else None,
    }


def verify_plan_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    """Walk a plan's critical points and check each against evidence.

    Args:
      plan_id: str — required.
      evidence: list[dict | str] — optional. Each item is searched for
        the CP's verification_hint. A CP is satisfied when at least one
        evidence item contains its hint (case-insensitive substring).
        May be omitted when auto_discover is True.
      auto_discover: bool — optional, default False. When True, the
        verifier reads the project's pytest-json-report at
        `report_path` (or the resolved default) and adds its `tests`
        list to the evidence stream. Best-effort: missing or malformed
        reports are silently skipped rather than failing the call.
      report_path: str — optional. Override the default report.json
        location. Useful for non-pytest runners or custom layouts.
        Resolved order when omitted:
          1. `MK_QA_REPORT_PATH` env override
          2. `<QA_PROJECT_ROOT>/report.json`
          3. `./report.json`

    Returns a structured checklist with per-CP satisfaction + an
    overall status:
      - "passed" : every CP satisfied
      - "incomplete" : some CPs unsatisfied
      - "failed" : no CPs satisfied (or no evidence at all)

    v0.9.2 adds an `evidence_sources` field to the response describing
    where the matched evidence came from (explicit / autodiscovered /
    both / none).
    """
    arguments = arguments or {}
    plan_id = arguments.get("plan_id")
    if not plan_id or not isinstance(plan_id, str):
        return {
            "error": "no_plan_id",
            "retryable": False,
            "hint": "verify_plan requires a `plan_id` returned by qa_plan.",
        }

    plan, plan_source = _fetch_plan(plan_id)
    if plan is None:
        return {
            "error": "plan_not_found",
            "retryable": False,
            "hint": (
                f"Unknown plan_id {plan_id!r} (expired, evicted, or "
                f"never created). Plans live {_CACHE_TTL_SECONDS // 60} "
                f"minutes in memory; persisted plans survive process "
                f"restarts but still honor the original TTL on read. "
                f"Call qa_plan to start a fresh one."
            ),
        }

    explicit_evidence = arguments.get("evidence")
    auto_discover = bool(arguments.get("auto_discover", False))
    report_path_arg = arguments.get("report_path")

    if explicit_evidence is None and not auto_discover:
        return {
            "error": "no_evidence",
            "retryable": False,
            "hint": (
                "verify_plan needs either an explicit `evidence` list "
                "(may be empty) OR `auto_discover=true` to read evidence "
                "from the project's report.json. Pass at least one."
            ),
        }
    if explicit_evidence is not None and not isinstance(explicit_evidence, list):
        return {
            "error": "bad_evidence",
            "retryable": False,
            "hint": (
                f"evidence must be a list, got {type(explicit_evidence).__name__}"
            ),
        }

    # ---- assemble evidence stream ----
    evidence: list[Any] = list(explicit_evidence) if explicit_evidence else []
    sources: dict[str, Any] = {
        "explicit_count": len(evidence),
        "autodiscovered": False,
        "autodiscovered_count": 0,
        "report_path": None,
    }
    if auto_discover:
        resolved_report_path: Path | None
        if isinstance(report_path_arg, str) and report_path_arg.strip():
            resolved_report_path = Path(report_path_arg).expanduser().resolve()
        else:
            resolved_report_path = _default_report_path()
        autodiscovered_rows, source = _autodiscover_evidence(resolved_report_path)
        if autodiscovered_rows:
            evidence.extend(autodiscovered_rows)
            sources["autodiscovered"] = True
            sources["autodiscovered_count"] = len(autodiscovered_rows)
            sources["report_path"] = source
        else:
            # Auto-discover requested but no rows found — surface the
            # path we LOOKED at so the user can diagnose.
            sources["report_path"] = str(resolved_report_path)

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
        "evidence_sources": sources,
        "plan_source": plan_source,  # "memory" or "disk"
        "verified_at": _now().isoformat(),
    }
