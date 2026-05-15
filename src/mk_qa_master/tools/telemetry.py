"""Append-only telemetry — feeds the optimizer's self-improvement analysis.

Three JSONL streams under TELEMETRY_DIR:
  - tool-usage.jsonl: every call_tool invocation (name, args_hash, duration, error)
  - generation-log.jsonl: every generate_test invocation
  - discovered-modules.jsonl: every successful analyze_url result

Why JSONL append-only: grep/jq friendly, survives partial writes, trivial to
trend over time without a DB. Reads cap at last N for analytics.
"""
import hashlib
import json
from datetime import datetime
from pathlib import Path

from ..config import (
    TELEMETRY_DIR,
    TOOL_USAGE_LOG,
    GENERATION_LOG,
    MODULES_LOG,
)


def _ensure_dir() -> None:
    try:
        TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


def _append(path: Path, record: dict) -> None:
    _ensure_dir()
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass


def log_tool_call(name: str, args: dict, duration_ms: int, error_type: str | None) -> None:
    try:
        args_blob = json.dumps(args or {}, sort_keys=True, default=str)
    except (TypeError, ValueError):
        args_blob = repr(args)
    args_hash = hashlib.sha1(args_blob.encode("utf-8", errors="replace")).hexdigest()[:12]
    _append(TOOL_USAGE_LOG, {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "tool": name,
        "args_hash": args_hash,
        "duration_ms": duration_ms,
        "error_type": error_type,
    })


def log_generation(filename: str, description: str, source: str = "manual") -> None:
    _append(GENERATION_LOG, {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "filename": filename,
        "description": (description or "")[:200],
        "source": source,  # "manual" or "analyze_url:<url>" etc.
    })


def log_discovered_modules(url: str, modules: list[dict]) -> None:
    _append(MODULES_LOG, {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "url": url,
        "module_names": [m.get("name") for m in (modules or []) if isinstance(m, dict) and m.get("name")],
    })


def read_recent(path: Path, limit: int = 500) -> list[dict]:
    """Return the last `limit` records from a JSONL file. Tolerant of bad lines."""
    if not path.exists():
        return []
    records: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return records[-limit:]
