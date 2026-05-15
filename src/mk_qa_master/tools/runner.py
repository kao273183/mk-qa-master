from ..runners import get_runner
from ..security import validate_filter


def list_tests() -> str:
    return get_runner().list_tests()


def run_tests(filter=None, headed=False, browser="chromium") -> dict:
    ok, err = validate_filter(filter)
    if not ok:
        return {"error": err}
    return get_runner().run_tests(filter=filter, headed=headed, browser=browser)


def run_failed() -> dict:
    return get_runner().run_failed()
