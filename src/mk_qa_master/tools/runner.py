from ..runners import get_runner
from ..security import validate_filter


def list_tests() -> str:
    return get_runner().list_tests()


def run_tests(filter=None, headed=False, browser="chromium",
              plan_id: str | None = None) -> dict:
    ok, err = validate_filter(filter)
    if not ok:
        return {"error": err}
    result = get_runner().run_tests(filter=filter, headed=headed, browser=browser)
    # v0.10.0 PR-1 — universal bookend (theme A, prd-v0.10-universal-bookend.md).
    # When the caller threads a plan_id through, we tack on a
    # `plan_verification` envelope so the same workflow used by
    # run_api_security_scan (v0.9.4) works here: declare CPs up front
    # via qa_plan → run → auto-verify against the freshly-written
    # report.json. We use verify_plan's `auto_discover` path rather than
    # re-reading report.json ourselves — verify_plan already owns the
    # report-path resolution chain (`MK_QA_REPORT_PATH` → project root →
    # cwd) and re-implementing that here would drift.
    if plan_id and isinstance(result, dict) and "error" not in result:
        from .qa_plan import verify_plan_tool
        verify_result = verify_plan_tool({
            "plan_id": plan_id,
            "auto_discover": True,
        })
        # If verify_plan errored (plan_not_found, expired, etc.), surface
        # its envelope under `plan_verification` rather than masking it
        # — the run itself succeeded; only the verification step had a
        # problem. Mirrors the v0.9.4 api_security pattern exactly.
        result["plan_verification"] = verify_result
    return result


def run_failed() -> dict:
    return get_runner().run_failed()
