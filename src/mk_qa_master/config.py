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

# Language for the built-in QA methodology layer served by get_qa_context()
# when the project has no qa-knowledge.md yet (and used as the starter
# template body by init_qa_knowledge). v0.6.2 ships English as the
# default for international reach; existing zh-TW users opt in by setting
# `QA_LANG=zh-tw`. Aliases (zh / zh_TW / CN) normalize to `zh-tw`;
# any other value falls back to `en` rather than raising — we'd rather
# serve the wrong language than crash the server boot.
QA_LANG = os.getenv("QA_LANG", "en").lower().strip()
if QA_LANG in ("zh", "zh-cn", "zh_cn", "cn", "zh_tw"):
    QA_LANG = "zh-tw"
if QA_LANG not in ("en", "zh-tw"):
    QA_LANG = "en"

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

# ---- API testing (QA_RUNNER=newman) ---------------------------------------
# Required when QA_RUNNER=newman. Plain filesystem path to a Postman 2.x
# collection.json (no `file://` prefix — Newman doesn't need scheme
# disambiguation since collections are always local artifacts).
POSTMAN_COLLECTION = os.getenv("QA_POSTMAN_COLLECTION", "").strip()

# Optional Postman environment file (`-e <path>`). Contains per-environment
# variables (base URL, credentials) referenced from the collection via
# `{{var_name}}` placeholders.
POSTMAN_ENVIRONMENT = os.getenv("QA_POSTMAN_ENVIRONMENT", "").strip()

# Optional Postman globals file (`-g <path>`). Same shape as the environment
# file but scoped globally across workspaces.
POSTMAN_GLOBALS = os.getenv("QA_POSTMAN_GLOBALS", "").strip()

# Iteration count (`-n <N>`). Newman replays the whole collection N times;
# useful for soak tests and flake detection. Default 1.
try:
    POSTMAN_ITERATIONS = int(os.getenv("QA_POSTMAN_ITERATIONS", "1") or "1")
except ValueError:
    POSTMAN_ITERATIONS = 1

# Optional CSV of folder names to restrict the run to (`--folder <name>`
# repeated). Postman folders group requests; this is the equivalent of
# pytest's `-k` filter but at the collection-organization level.
POSTMAN_FOLDER = os.getenv("QA_POSTMAN_FOLDER", "").strip()

# Per-request timeout in milliseconds (`--timeout-request`). Default 30s;
# distinct from QA_TIMEOUT_SECONDS, which caps the *whole subprocess* on
# top of this.
try:
    POSTMAN_TIMEOUT_REQUEST_MS = int(os.getenv("QA_POSTMAN_TIMEOUT_REQUEST_MS", "30000") or "30000")
except ValueError:
    POSTMAN_TIMEOUT_REQUEST_MS = 30000


# ---- AI Visual Challenge Solver (v0.7.0) -----------------------------------
# Two-tool surface (`inspect_visual_challenge` + `solve_visual_challenge`)
# that lets a multimodal AI client work through reCAPTCHA v2 image-grid
# challenges via Playwright. Gated by an explicit consent env var so the
# default install never auto-solves a CAPTCHA — see PRD §13 + §21 for the
# ratified decisions on consent, allowlist, and hard-stop behavior.

# Master consent gate. Default `false`; must be explicitly set to `true`
# (or `1` / `yes`) for the visual challenge tools to do anything. Without
# this, both tools return a `consent_required` structured error carrying
# the legal disclaimer text from visual_challenge.DISCLAIMER_TEXT.
QA_VISUAL_CHALLENGE_CONSENT = os.getenv(
    "QA_VISUAL_CHALLENGE_CONSENT", ""
).strip().lower() in ("1", "true", "yes")

# Wall-clock budget (seconds) for the inspect→solve cycle. Honors
# QA_TIMEOUT_SECONDS as a hard ceiling at the runner level. Default 120s
# matches reCAPTCHA's "two minutes" countdown so the budget never
# outlasts the challenge itself.
try:
    QA_VISUAL_CHALLENGE_TIMEOUT = int(
        os.getenv("QA_VISUAL_CHALLENGE_TIMEOUT", "120") or "120"
    )
except ValueError:
    QA_VISUAL_CHALLENGE_TIMEOUT = 120

# Optional comma-separated domain allowlist. When SET, the tools refuse
# to operate on any page whose host doesn't match an entry (suffix
# match). When UNSET (the default), the tools warn-only — they proceed
# but include a `warning` field in the response payload nudging the
# user to set an allowlist. This split was ratified in PRD §21 #3:
# strict-block when set, warn-only when unset, so single-user dev
# ergonomics stay clean while shared CI / multi-tenant environments
# have an explicit safety net.
_authorized = os.getenv("QA_VISUAL_CHALLENGE_AUTHORIZED_DOMAINS", "").strip()
QA_VISUAL_CHALLENGE_AUTHORIZED_DOMAINS: frozenset[str] | None = (
    frozenset(d.strip().lower() for d in _authorized.split(",") if d.strip())
    if _authorized else None  # None = unset = warn-only mode
)


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
