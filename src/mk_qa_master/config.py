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
