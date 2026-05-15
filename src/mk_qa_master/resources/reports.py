"""Resource providers for test reports and artifacts."""

from ..config import REPORT_PATH, ARTIFACTS_DIR


def latest_report_uri() -> str:
    return "report://latest"


def read_latest_report() -> str:
    if not REPORT_PATH.exists():
        return "{}"
    return REPORT_PATH.read_text()


def list_trace_files() -> list[dict]:
    if not ARTIFACTS_DIR.exists():
        return []
    return [
        {"uri": f"trace://{p.stem}", "name": p.name, "path": str(p)}
        for p in ARTIFACTS_DIR.glob("**/trace.zip")
    ]
