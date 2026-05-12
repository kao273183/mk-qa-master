import inspect

from ..runners import get_runner


def generate_test(
    description: str,
    filename: str,
    url: str | None = None,
    module: dict | None = None,
    business_context: str | None = None,
) -> str:
    """Pass url/module/business_context through only to runners that declare them.

    Why inspect: other runners (jest/cypress/go_test) keep the narrow
    (description, filename) signature. Calling them with extra kwargs would
    raise TypeError. Sniffing the signature lets us stay graceful.
    """
    runner = get_runner()
    sig = inspect.signature(runner.generate_test)
    extra: dict = {}
    if "url" in sig.parameters:
        extra["url"] = url
    if "module" in sig.parameters:
        extra["module"] = module
    if "business_context" in sig.parameters:
        extra["business_context"] = business_context
    return runner.generate_test(description, filename, **extra)


def codegen(url: str, output: str = "recorded_test.py") -> str:
    return get_runner().codegen(url, output)
