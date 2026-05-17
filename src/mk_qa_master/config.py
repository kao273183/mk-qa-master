from pathlib import Path
import os
import shutil
import subprocess

PROJECT_ROOT = Path(os.getenv("QA_PROJECT_ROOT", "./tests_project")).resolve()

RUNNER_NAME = os.getenv("QA_RUNNER", "pytest").lower()

REPORT_PATH = PROJECT_ROOT / "report.json"
JUNIT_PATH = PROJECT_ROOT / "junit.xml"
ARTIFACTS_DIR = PROJECT_ROOT / "test-results"
HISTORY_DIR = ARTIFACTS_DIR / "history"
TELEMETRY_DIR = ARTIFACTS_DIR / "telemetry"
TOOL_USAGE_LOG = TELEMETRY_DIR / "tool-usage.jsonl"
GENERATION_LOG = TELEMETRY_DIR / "generation-log.jsonl"
MODULES_LOG = TELEMETRY_DIR / "discovered-modules.jsonl"
OPTIMIZATION_PATH = PROJECT_ROOT / "optimization-plan.md"
# Business / domain knowledge file — drives "real QA" generation, escaping
# the rule-based monkey-testing default. Override via QA_KNOWLEDGE_FILE env.
QA_KNOWLEDGE_PATH = Path(
    os.getenv("QA_KNOWLEDGE_FILE", str(PROJECT_ROOT / "qa-knowledge.md"))
).resolve()

# Optional remote-ADB endpoint (BlueStacks / Genymotion / Nox / LDPlayer /
# WSA / cloud farms). When set, runners auto-run `adb connect <host>`
# before invoking Maestro so `adb devices` actually lists the instance.
# Example: QA_ANDROID_HOST=127.0.0.1:5555 (BlueStacks default).
ANDROID_HOST = os.getenv("QA_ANDROID_HOST", "").strip()

# ---- API testing (QA_RUNNER=schemathesis) ---------------------------------
# Required when QA_RUNNER=schemathesis. Accepts http(s):// and file:// only;
# plain paths must use the file:// prefix to avoid relative/absolute
# ambiguity. Validated at runner first-use (not import-time) so the rest of
# the MCP boots even when API config is unset.
OPENAPI_URL = os.getenv("QA_OPENAPI_URL", "").strip()

# Comma-separated subset of Schemathesis checks. Empty → `--checks all`.
# Examples: "response_schema_conformance,status_code_conformance".
SCHEMATHESIS_CHECKS = os.getenv("QA_SCHEMATHESIS_CHECKS", "").strip()

# Authorization header value (the part after "Authorization:"). Passed as
# `-H "Authorization: <value>"`. Never logged; redaction in the runner
# scrubs it from request/response captures before they hit disk.
SCHEMATHESIS_AUTH = os.getenv("QA_SCHEMATHESIS_AUTH", "").strip()

# Hypothesis examples per operation. Higher = deeper fuzz, slower run.
# Schemathesis default is 100; we default to 20 to keep CI runs snappy.
try:
    SCHEMATHESIS_MAX_EXAMPLES = int(os.getenv("QA_SCHEMATHESIS_MAX_EXAMPLES", "20"))
except ValueError:
    SCHEMATHESIS_MAX_EXAMPLES = 20

# Dry-run mode — Schemathesis resolves the schema and plans requests but
# never issues HTTP. Use this when pointing at a production API for a
# safety preview, or when the runner is wired into CI against a schema-only
# artifact (no live server).
SCHEMATHESIS_DRY_RUN = os.getenv("QA_SCHEMATHESIS_DRY_RUN", "").lower() in ("1", "true", "yes")

# Disable secret redaction in archived reports. Default off (redaction on).
# Flip to "1" only for short debug sessions; archived reports may be shared.
NO_REDACT = os.getenv("QA_NO_REDACT", "").lower() in ("1", "true", "yes")


def connect_android_host(timeout_s: float = 10.0) -> tuple[bool, str]:
    """Ensure the configured remote-ADB endpoint is paired before Maestro runs.

    BlueStacks / Genymotion expose Android over TCP rather than auto-discovery;
    Maestro can't see them until `adb connect host:port` has succeeded once
    per session. `adb connect` is idempotent (returns "already connected"
    on repeat calls), so wiring this at the runner boundary costs nothing
    when the endpoint is already paired.

    Returns (ok, message). When QA_ANDROID_HOST is unset, returns
    (True, "") — callers can skip surfacing anything in that case.
    """
    if not ANDROID_HOST:
        return True, ""
    if not shutil.which("adb"):
        return (
            False,
            "QA_ANDROID_HOST is set but `adb` is not on PATH — install Android Platform-Tools",
        )
    try:
        result = subprocess.run(
            ["adb", "connect", ANDROID_HOST],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"adb connect {ANDROID_HOST} failed: {type(e).__name__}: {e}"
    out = (result.stdout + result.stderr).strip()
    low = out.lower()
    if "connected to" in low or "already connected" in low:
        return True, out
    return False, out or f"adb connect {ANDROID_HOST} returned no output"
