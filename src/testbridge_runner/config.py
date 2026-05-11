from pathlib import Path
import os

PROJECT_ROOT = Path(os.getenv("QA_PROJECT_ROOT", "./tests_project")).resolve()

RUNNER_NAME = os.getenv("QA_RUNNER", "pytest").lower()

REPORT_PATH = PROJECT_ROOT / "report.json"
ARTIFACTS_DIR = PROJECT_ROOT / "test-results"
