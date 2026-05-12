from pathlib import Path
import os

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
