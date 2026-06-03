"""v1.1.0 — pytest fixtures for generated Edge runner tests.

Generated tests import these via:

    from mk_qa_master.edge.pytest_plugin import backend, stream, latency  # noqa: F401

The three fixtures the spec §8 mandates:

  - `backend` (session-scoped) — InferenceBackend constructed from
    EdgeConfig. Heavy: in desktop mode this loads ultralytics + a
    YOLO model file, so it's session-scoped to amortize across tests.
  - `stream` (function-scoped) — `cv2.VideoCapture(EDGE_RTSP_URL)`.
    Function-scoped because cv2.VideoCapture holds an open socket
    that grumbles when reused across tests.
  - `latency` (function-scoped) — fresh `LatencyTracker` per test.

`cv2` is imported lazily so `import mk_qa_master.edge.pytest_plugin`
on a base install (no [edge] extras) doesn't crash — only the
`stream` fixture body fails, with a clear message about the
missing extras.
"""
from __future__ import annotations

import os

import pytest

from .inference import make_backend
from .metrics import LatencyTracker
from ..config import EdgeConfig


@pytest.fixture(scope="session")
def backend():
    """Session-scoped InferenceBackend. Constructed once per pytest
    invocation — for LocalYolo that means one model load, not one
    per test.
    """
    return make_backend(EdgeConfig())


@pytest.fixture
def stream():
    """RTSP / file capture handle. The runner's _edge_env() sets
    EDGE_RTSP_URL pre-pytest; we read it directly so the fixture
    doesn't need a reference to the runner instance.

    `cv2` is imported here (not at module load) so the plugin module
    can be imported on a base install without the [edge] extras —
    failure is localized to tests that actually try to use the
    fixture.
    """
    try:
        import cv2  # type: ignore[import-not-found]
    except ImportError as e:  # pragma: no cover — install-time guidance
        pytest.skip(
            "Edge runner stream fixture requires opencv-python. "
            'Install with: pip install "mk-qa-master[edge]". '
            f"Underlying ImportError: {e}"
        )

    url = os.environ.get("EDGE_RTSP_URL", "")
    if not url:
        pytest.skip(
            "EDGE_RTSP_URL not set — the EdgeInferenceRunner populates "
            "it before invoking pytest. If you're running this test "
            "directly, set QA_RTSP_SOURCE before invoking."
        )

    cap = cv2.VideoCapture(url)
    assert cap.isOpened(), f"Could not open RTSP stream: {url!r}"
    try:
        yield cap
    finally:
        cap.release()


@pytest.fixture
def latency():
    """Fresh LatencyTracker per test — assertions like
    `assert latency.p95() <= SLA` need a clean window per case."""
    return LatencyTracker()
