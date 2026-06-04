"""v1.4.0 — `mk-qa-master doctor` environment diagnostic.

The motivating case: a user installs the base package (`pip install
mk-qa-master`), starts the MCP server, calls `analyze_stream`, and hits
`missing_extras` because `[edge]` wasn't installed. The README + error
envelope already document the fix, but the friction is real — the
doctor pulls every diagnosis into one command:

    $ mk-qa-master doctor

Checks are grouped:

  * **System**: Python version (≥ 3.10) + ffmpeg + mediamtx on PATH
  * **Core deps**: imports we always require (mcp, jsonschema)
  * **Edge extras `[edge]`**: opencv-python (cv2), ultralytics, numpy
  * **Runners**: enumerate the `RUNNER_REGISTRY`
  * **MCP surface**: tool count sanity check

Severity levels (used to compute exit code + summary line):

  * `ok`   — check passed
  * `warn` — advisory; missing optional feature (e.g. `[edge]` extras
    not installed → only matters if you use the edge runner)
  * `fail` — critical; mk-qa-master can't function normally

Exit code is `1` if **any** check is `fail`; otherwise `0` (warnings
don't fail the command — they're informational).

Output modes:

  * **plain** (default): human-readable, grouped, ASCII-safe glyphs
  * `--json`: one machine-readable dict, useful for CI pre-flight
    gates and host-LLM consumption
"""
from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import platform
import shutil
import sys
from dataclasses import dataclass, asdict
from typing import Literal


Severity = Literal["ok", "warn", "fail"]


@dataclass
class CheckResult:
    """One row in the doctor report.

    `name` is the human-readable label (e.g. "Python 3.10+", "ffmpeg",
    "opencv-python"). `detail` carries the version or "not found" / "
    missing" descriptor. `hint` (when set) is the concrete fix suggestion
    — shown both inline in plain mode and aggregated in the bottom
    summary block.
    """
    section: str
    name: str
    severity: Severity
    detail: str
    hint: str = ""


_MIN_PYTHON = (3, 10)


def _check_python() -> CheckResult:
    v = sys.version_info
    detail = f"{v.major}.{v.minor}.{v.micro} ({platform.machine()})"
    if (v.major, v.minor) < _MIN_PYTHON:
        return CheckResult(
            section="System", name="Python ≥ 3.10",
            severity="fail", detail=detail,
            hint=f"mk-qa-master requires Python ≥ {_MIN_PYTHON[0]}.{_MIN_PYTHON[1]}.",
        )
    return CheckResult(
        section="System", name="Python ≥ 3.10",
        severity="ok", detail=detail,
    )


def _check_bin(name: str, install_hint: str, severity_when_missing: Severity = "warn") -> CheckResult:
    """Check whether a system binary is on PATH."""
    path = shutil.which(name)
    if path:
        return CheckResult(
            section="System", name=name,
            severity="ok", detail=path,
        )
    return CheckResult(
        section="System", name=name,
        severity=severity_when_missing,
        detail="not on PATH",
        hint=install_hint,
    )


def _check_import(
    module_name: str,
    section: str,
    *,
    install_hint: str,
    severity_when_missing: Severity = "warn",
    pkg_name: str | None = None,
) -> CheckResult:
    """Try `importlib.import_module(module_name)`; surface the version
    via `importlib.metadata.version(pkg_name)` when available.

    `pkg_name` defaults to `module_name` but is split out so e.g. cv2 →
    opencv-python can be reported cleanly.
    """
    try:
        importlib.import_module(module_name)
    except ImportError:
        return CheckResult(
            section=section, name=module_name,
            severity=severity_when_missing,
            detail="not installed",
            hint=install_hint,
        )
    try:
        version = importlib.metadata.version(pkg_name or module_name)
    except importlib.metadata.PackageNotFoundError:
        version = "?"
    return CheckResult(
        section=section, name=module_name,
        severity="ok", detail=version,
    )


def _check_runners() -> list[CheckResult]:
    """Enumerate `RUNNER_REGISTRY` and report each entry as a row.

    Registry lookup failure is itself a fail row — mk-qa-master can't
    run tests if the registry import blows up.
    """
    try:
        from .runners import REGISTRY
    except ImportError as e:
        return [CheckResult(
            section="Runners", name="registry",
            severity="fail", detail=f"import failed: {e}",
            hint="reinstall mk-qa-master; this should never happen on a clean install.",
        )]
    rows: list[CheckResult] = []
    seen_classes: set[str] = set()
    for alias in sorted(REGISTRY.keys()):
        try:
            runner_cls = REGISTRY[alias]
            cls_name = runner_cls.__name__
            # Suppress alias duplicates (e.g. edge/rtsp point at same class).
            tag = f"alias:{cls_name}" if cls_name in seen_classes else cls_name
            seen_classes.add(cls_name)
            rows.append(CheckResult(
                section="Runners", name=alias,
                severity="ok", detail=tag,
            ))
        except Exception as e:
            rows.append(CheckResult(
                section="Runners", name=alias,
                severity="fail", detail=f"resolve failed: {e}",
            ))
    return rows


def _check_mcp_surface() -> CheckResult:
    """Sanity check on the MCP tool surface. v1.0 promises 22 tools.
    Drift here without an intentional schema evolution is a deployment
    smell — though it's not enforced as `fail` because v1.x reserves
    the right to add tools (additive-only — the stability lock catches
    removals separately via the snapshot test)."""
    try:
        import asyncio
        from .server import list_tools
        tools = asyncio.run(list_tools())
    except Exception as e:
        return CheckResult(
            section="MCP surface", name="tool list",
            severity="fail", detail=f"list_tools() failed: {e}",
            hint="reinstall mk-qa-master; check Python import errors above.",
        )
    return CheckResult(
        section="MCP surface", name="tool count",
        severity="ok", detail=f"{len(tools)} tools",
    )


def run_all_checks() -> list[CheckResult]:
    """Run every check in stable order; result list is what every output
    mode (plain + JSON) renders from."""
    results: list[CheckResult] = []
    # --- System ---
    results.append(_check_python())
    results.append(_check_bin(
        "ffmpeg",
        install_hint='macOS: brew install ffmpeg | Linux: apt-get install ffmpeg',
    ))
    results.append(_check_bin(
        "mediamtx",
        install_hint=(
            "macOS: brew install mediamtx | "
            "Linux: download from https://github.com/bluenviron/mediamtx/releases"
        ),
    ))
    # --- Core deps ---
    results.append(_check_import(
        "mcp", section="Core deps",
        install_hint="pip install mk-qa-master  # reinstall to restore core deps",
        severity_when_missing="fail",
    ))
    results.append(_check_import(
        "jsonschema", section="Core deps",
        install_hint="pip install mk-qa-master  # reinstall to restore core deps",
        severity_when_missing="fail",
    ))
    # --- Edge extras [edge] ---
    results.append(_check_import(
        "cv2", section="Edge extras [edge]",
        install_hint='pip install "mk-qa-master[edge]"',
        pkg_name="opencv-python",
    ))
    results.append(_check_import(
        "ultralytics", section="Edge extras [edge]",
        install_hint='pip install "mk-qa-master[edge]"',
    ))
    results.append(_check_import(
        "numpy", section="Edge extras [edge]",
        install_hint='pip install "mk-qa-master[edge]"',
    ))
    # --- Runners ---
    results.extend(_check_runners())
    # --- MCP surface ---
    results.append(_check_mcp_surface())
    return results


# ---------- Rendering ----------------------------------------------------

_GLYPH = {"ok": "✓", "warn": "!", "fail": "✗"}


def _version_string() -> str:
    try:
        return importlib.metadata.version("mk-qa-master")
    except importlib.metadata.PackageNotFoundError:
        return "?"


def render_plain(results: list[CheckResult]) -> str:
    """Group results by section, then list each row with a status glyph.

    Hints are deduplicated and printed in a bottom summary block when
    any non-`ok` result exists — that's the part operators care about
    most when triaging a failed `analyze_stream` call.
    """
    lines: list[str] = [f"mk-qa-master v{_version_string()} — environment doctor", ""]
    current_section = ""
    for r in results:
        if r.section != current_section:
            current_section = r.section
            lines.append(current_section)
        glyph = _GLYPH[r.severity]
        line = f"  {glyph} {r.name}: {r.detail}"
        if r.hint and r.severity != "ok":
            line += f"  — {r.hint}"
        lines.append(line)
    lines.append("")
    fails = [r for r in results if r.severity == "fail"]
    warns = [r for r in results if r.severity == "warn"]
    if fails or warns:
        lines.append("─" * 6)
        if fails:
            lines.append(f"{len(fails)} critical issue(s):")
            for r in fails:
                lines.append(f"  ✗ {r.section}/{r.name}: {r.hint or r.detail}")
        if warns:
            lines.append(f"{len(warns)} warning(s) — only matters if you use the affected feature:")
            for r in warns:
                lines.append(f"  ! {r.section}/{r.name}: {r.hint or r.detail}")
    else:
        lines.append("All clear.")
    return "\n".join(lines)


def render_json(results: list[CheckResult]) -> str:
    """Machine-readable form: one JSON object so a CI gate or host LLM
    can ingest it directly. `summary` summarizes counts at the top so a
    consumer can short-circuit without walking every row."""
    payload = {
        "version": _version_string(),
        "summary": {
            "ok": sum(1 for r in results if r.severity == "ok"),
            "warn": sum(1 for r in results if r.severity == "warn"),
            "fail": sum(1 for r in results if r.severity == "fail"),
        },
        "results": [asdict(r) for r in results],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _exit_code(results: list[CheckResult]) -> int:
    """1 if any `fail`; 0 otherwise. Warnings never fail the command —
    they're informational (e.g. `[edge]` extras not installed is fine
    for users who only use pytest)."""
    return 1 if any(r.severity == "fail" for r in results) else 0


def main(argv: list[str] | None = None) -> int:
    """`mk-qa-master doctor` CLI entry. Returns the exit code so the
    server dispatch layer can pass it through."""
    parser = argparse.ArgumentParser(
        prog="mk-qa-master doctor",
        description="Diagnose mk-qa-master installation + environment.",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Emit machine-readable JSON instead of grouped plain text.",
    )
    args = parser.parse_args(argv)
    results = run_all_checks()
    print(render_json(results) if args.json else render_plain(results))
    return _exit_code(results)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
