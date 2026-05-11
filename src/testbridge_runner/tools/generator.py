from ..runners import get_runner


def generate_test(description: str, filename: str) -> str:
    return get_runner().generate_test(description, filename)


def codegen(url: str, output: str = "recorded_test.py") -> str:
    return get_runner().codegen(url, output)
