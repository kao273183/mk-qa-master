from .base import TestRunner
from .pytest_playwright import PytestPlaywrightRunner
from .jest import JestRunner
from .cypress import CypressRunner
from .go_test import GoTestRunner
from .maestro import MaestroRunner
from .schemathesis import SchemathesisRunner
from .newman import NewmanRunner
from .edge_inference import EdgeInferenceRunner
from ..config import RUNNER_NAME

REGISTRY: dict[str, type[TestRunner]] = {
    "pytest": PytestPlaywrightRunner,
    "pytest-playwright": PytestPlaywrightRunner,
    "playwright": PytestPlaywrightRunner,
    "jest": JestRunner,
    "cypress": CypressRunner,
    "go": GoTestRunner,
    "go-test": GoTestRunner,
    "maestro": MaestroRunner,
    "mobile": MaestroRunner,
    "schemathesis": SchemathesisRunner,
    "api": SchemathesisRunner,
    "newman": NewmanRunner,
    "postman": NewmanRunner,
    # v1.1.0 — Edge AI Inference Runner (Theme G, Phase 1+2). Aliased
    # `rtsp` for muscle memory; both names construct the same class.
    "edge": EdgeInferenceRunner,
    "rtsp": EdgeInferenceRunner,
}

# v1.5.0 — snapshot of the built-in names before plugins are merged in.
# Plugin runners may never shadow these (see plugins.py).
BUILTIN_REGISTRY: dict[str, type[TestRunner]] = dict(REGISTRY)


def _merge_plugin_runners() -> None:
    """Add runners contributed via the `mk_qa_master.runners` entry-point
    group. Idempotent; built-in names always win."""
    from ..plugins import get_state

    for name, cls in get_state().runners.items():
        REGISTRY.setdefault(name, cls)


def get_runner() -> TestRunner:
    cls = REGISTRY.get(RUNNER_NAME)
    if not cls:
        _merge_plugin_runners()
        cls = REGISTRY.get(RUNNER_NAME)
    if not cls:
        raise ValueError(
            f"未知的 QA_RUNNER: {RUNNER_NAME}。可用: {sorted(REGISTRY)}"
        )
    return cls()


def current_runner_name() -> str:
    """The configured QA_RUNNER value (used to scope plugin tools)."""
    return RUNNER_NAME


__all__ = ["TestRunner", "get_runner", "REGISTRY", "BUILTIN_REGISTRY", "current_runner_name"]
