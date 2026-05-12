from .base import TestRunner
from .pytest_playwright import PytestPlaywrightRunner
from .jest import JestRunner
from .cypress import CypressRunner
from .go_test import GoTestRunner
from ..config import RUNNER_NAME

REGISTRY: dict[str, type[TestRunner]] = {
    "pytest": PytestPlaywrightRunner,
    "pytest-playwright": PytestPlaywrightRunner,
    "playwright": PytestPlaywrightRunner,
    "jest": JestRunner,
    "cypress": CypressRunner,
    "go": GoTestRunner,
    "go-test": GoTestRunner,
}


def get_runner() -> TestRunner:
    cls = REGISTRY.get(RUNNER_NAME)
    if not cls:
        raise ValueError(
            f"未知的 QA_RUNNER: {RUNNER_NAME}。可用: {sorted(REGISTRY)}"
        )
    return cls()


__all__ = ["TestRunner", "get_runner", "REGISTRY"]
