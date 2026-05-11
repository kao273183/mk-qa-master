from ..runners import get_runner


def get_report_summary() -> dict:
    return get_runner().get_report_summary()


def get_failure_details(test_id: str | None = None) -> list[dict]:
    return get_runner().get_failure_details(test_id)
