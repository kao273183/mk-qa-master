"""Security guardrails for user-controlled inputs.

The MCP server receives arguments parsed out of natural-language prompts,
so anything that ends up in subprocess argv or as a file path must be
validated. Two concrete attack shapes worth caring about:

  1. Path traversal — `generate_test(filename="../../../../etc/passwd")`
     would otherwise let a prompt write arbitrary files inside the user's
     home directory.
  2. Argument injection — `run_tests(filter="--config=/tmp/evil")` could
     smuggle a flag past the runner's intended `-k` / `-run` / `-t`
     consumer, turning a filter into a flag the underlying tool honors.

Subprocess is always invoked with a list (`shell=False`), so classical
shell-metachar attacks (`;`, backticks, `$()`, pipes) are out of scope.
This module focuses on the two cases above, plus a global subprocess
timeout to keep runaway tests from blocking the MCP server forever.
"""
import os
import re
import subprocess
from pathlib import Path


# Default subprocess timeout. Mobile flows on slow emulators or CI
# legitimately take minutes; 10 min leaves headroom while still capping
# pathological hangs (looping retries, dead simulators, network stalls).
# Override via env var for CI / longer suites.
DEFAULT_TIMEOUT_SECONDS = int(os.getenv("QA_TIMEOUT_SECONDS", "600"))

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
_MAX_FILTER_LEN = 200
_MAX_FILENAME_LEN = 200


def validate_filter(value: object) -> tuple[bool, str | None]:
    """Check a `filter` argument before it reaches subprocess argv.

    Pytest / Go / Jest / Cypress all accept fairly free-form filter
    syntax (regex, boolean expressions, globs), so the rule is *minimal*:
    reject the cases that smell like attacks, allow everything else.

    Returns (ok, error_message). When ok is True, caller passes the
    original value through unchanged; when False, caller surfaces the
    error string as a tool-result error.
    """
    if value is None or value == "":
        return True, None
    if not isinstance(value, str):
        return False, f"filter must be a string, got {type(value).__name__}"
    if len(value) > _MAX_FILTER_LEN:
        return False, f"filter too long ({len(value)} > {_MAX_FILTER_LEN} chars)"
    if value.startswith("-"):
        # A leading `-` would slot in as a new CLI option to the underlying
        # tool — e.g. `pytest -k --config=evil` parses `--config=evil` as a
        # pytest option, not as part of -k. Block at the boundary.
        return False, (
            "filter cannot start with '-' (looks like a CLI option, "
            "not a test name)"
        )
    if ".." in value:
        # Filters are not paths; '..' in them is almost always a sign that
        # someone is trying to escape an expected scope (cypress turns the
        # filter into a glob, where `..` would walk outside the project).
        return False, "filter cannot contain '..'"
    if _CONTROL_CHARS_RE.search(value):
        return False, "filter contains control characters"
    return True, None


def validate_filename(
    filename: object, project_root: Path
) -> tuple[bool, str | Path]:
    """Resolve filename relative to project_root, ensuring it stays inside.

    Returns (ok, target_path_or_error). On ok=True, the second value is
    the safe absolute Path callers should write to. On ok=False, the
    second value is a human-readable error string.
    """
    if not isinstance(filename, str) or not filename:
        return False, "filename must be a non-empty string"
    if len(filename) > _MAX_FILENAME_LEN:
        return False, f"filename too long ({len(filename)} > {_MAX_FILENAME_LEN} chars)"
    if _CONTROL_CHARS_RE.search(filename):
        return False, "filename contains control characters"
    p = Path(filename)
    if p.is_absolute():
        return False, "filename must be relative to project root, not absolute"
    root = project_root.resolve()
    target = (root / p).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return False, f"filename escapes project root: {filename!r}"
    return True, target


def safe_run(cmd: list[str], *, timeout: float | None = None, **kwargs):
    """`subprocess.run` wrapper with a default timeout and clean timeout handling.

    Returns a `CompletedProcess` either from the real run or — on timeout
    — a synthetic one with returncode 124 (GNU timeout convention) and
    a clearly-tagged stderr. That keeps callsites uniform: they don't
    need to special-case TimeoutExpired, they just see a non-zero exit
    with a recognizable message.
    """
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    effective_timeout = timeout if timeout is not None else DEFAULT_TIMEOUT_SECONDS
    try:
        return subprocess.run(cmd, timeout=effective_timeout, **kwargs)
    except subprocess.TimeoutExpired as e:
        def _decode(x):
            if x is None:
                return ""
            if isinstance(x, bytes):
                try:
                    return x.decode("utf-8", errors="replace")
                except Exception:
                    return ""
            return str(x)

        return subprocess.CompletedProcess(
            args=cmd,
            returncode=124,
            stdout=_decode(e.stdout),
            stderr=(
                f"[TIMEOUT after {effective_timeout}s — increase QA_TIMEOUT_SECONDS "
                f"to allow longer runs]\n"
            )
            + _decode(e.stderr),
        )
