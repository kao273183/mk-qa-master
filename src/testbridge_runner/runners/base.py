from abc import ABC, abstractmethod


class TestRunner(ABC):
    """通用測試 runner 介面。每個測試框架實作一個子類。"""

    name: str = "base"

    @abstractmethod
    def list_tests(self) -> str: ...

    @abstractmethod
    def run_tests(self, filter: str | None = None, **kwargs) -> dict: ...

    @abstractmethod
    def run_failed(self) -> dict: ...

    @abstractmethod
    def get_report_summary(self) -> dict: ...

    @abstractmethod
    def get_failure_details(self, test_id: str | None = None) -> list[dict]: ...

    @abstractmethod
    def generate_test(self, description: str, filename: str) -> str: ...

    def codegen(self, url: str, output: str = "recorded_test.py") -> str:
        return f"{self.name} runner 不支援 codegen"

    def get_history(self, limit: int = 10) -> list[dict]:
        """Return chronologically-ordered (oldest first) past run summaries.

        Default: empty — runners override to expose trends.
        """
        return []
