from ..runners import get_runner


def list_tests() -> str:
    return get_runner().list_tests()


def run_tests(filter=None, headed=False, browser="chromium") -> dict:
    return get_runner().run_tests(filter=filter, headed=headed, browser=browser)


def run_failed() -> dict:
    return get_runner().run_failed()
