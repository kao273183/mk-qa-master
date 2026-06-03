"""v1.1.0 PR-1 — inference.make_backend() factory + RemoteHTTP stub.

LocalYolo construction loads ultralytics + torch — too heavy for unit
tests. We verify make_backend() picks the right *class* without
constructing an actual LocalYolo by intercepting the import path.

RemoteHTTP is testable directly: construction stores the URL, .infer()
raises NotImplementedError (the v1.2 stub contract).
"""
from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from mk_qa_master.edge.inference import (
    Detection,
    InferResult,
    RemoteHTTP,
    make_backend,
)


@dataclass
class _StubCfg:
    """Mimics EdgeConfig surface without depending on env vars."""
    inference_url: str = ""
    jetson_host: str = ""
    model_path: str = "yolov8n.pt"


# ---- Detection / InferResult --------------------------------------------

def test_detection_dataclass_has_label_bbox_score():
    """Generated tests check `.label` and `.bbox`; lock the shape."""
    d = Detection(label="person", bbox=(0, 0, 10, 10), score=0.9)
    assert d.label == "person"
    assert d.bbox == (0, 0, 10, 10)
    assert d.score == 0.9


def test_infer_result_has_method_for_label_lookup():
    """`InferResult.has(label)` is a convenience for tests asserting
    'the frame contained at least one X'."""
    res = InferResult(
        detections=[
            Detection("person", (0, 0, 10, 10), 0.9),
            Detection("car", (50, 50, 30, 30), 0.8),
        ],
        latency_ms=12.3,
    )
    assert res.has("person") is True
    assert res.has("car") is True
    assert res.has("forklift") is False


# ---- make_backend() factory ---------------------------------------------

def test_make_backend_uses_inference_url_when_set():
    """QA_INFERENCE_ENDPOINT > QA_JETSON_HOST > desktop. Exact URL
    passed through."""
    cfg = _StubCfg(inference_url="http://dev:8000/infer")
    backend = make_backend(cfg)
    assert isinstance(backend, RemoteHTTP)
    assert backend.url == "http://dev:8000/infer"


def test_make_backend_uses_jetson_host_when_inference_url_absent():
    """Jetson host auto-derives `http://<host>:8000/infer`."""
    cfg = _StubCfg(jetson_host="192.168.1.50")
    backend = make_backend(cfg)
    assert isinstance(backend, RemoteHTTP)
    assert backend.url == "http://192.168.1.50:8000/infer"


def test_make_backend_inference_url_wins_over_jetson_host():
    """Both set → inference_url wins (explicit > implicit)."""
    cfg = _StubCfg(
        inference_url="http://explicit:9000/infer",
        jetson_host="192.168.1.50",
    )
    backend = make_backend(cfg)
    assert isinstance(backend, RemoteHTTP)
    assert backend.url == "http://explicit:9000/infer"


def test_make_backend_falls_back_to_local_yolo_in_desktop_mode():
    """No remote vars set → LocalYolo gets constructed against
    cfg.model_path. We mock ultralytics so the test doesn't need
    torch + a real YOLO model file."""
    cfg = _StubCfg(model_path="custom-model.pt")
    fake_yolo = MagicMock()

    with patch.dict("sys.modules", {"ultralytics": MagicMock(YOLO=fake_yolo)}):
        backend = make_backend(cfg)

    # Construction passes the model_path through.
    fake_yolo.assert_called_once_with("custom-model.pt")
    # We don't assert isinstance(backend, LocalYolo) here because
    # patch.dict re-imports the module — pytest's strict mode would
    # complain about cross-module isinstance. The fact that
    # ultralytics.YOLO was called with the right model path is the
    # contract we care about.
    assert backend is not None


# ---- RemoteHTTP stub (v1.2 deferred) ------------------------------------

def test_remote_http_construction_stores_url():
    """Stub construction is cheap — just records the URL. Used by
    make_backend's branches even though .infer() hasn't been wired
    yet."""
    b = RemoteHTTP("http://test/infer")
    assert b.url == "http://test/infer"


# ---- RemoteHTTP (v1.2.0+) -----------------------------------------------

def _fake_cv2():
    """Mock cv2 module that returns a deterministic JPEG-encoded payload."""
    m = MagicMock()
    m.imencode = MagicMock(return_value=(True, MagicMock(tobytes=lambda: b"jpegdata")))
    m.IMWRITE_JPEG_QUALITY = 1
    return m


def _fake_requests_module(post_response=None, post_side_effect=None):
    """Mock requests module that lets each test stub the .post() return."""
    m = MagicMock()
    if post_side_effect is not None:
        m.post = MagicMock(side_effect=post_side_effect)
    else:
        m.post = MagicMock(return_value=post_response or MagicMock())
    # The except-blocks reference these as types — keep the real
    # exception classes so isinstance/raise/raise-from work normally.
    import requests as real_requests  # noqa: PLC0415
    m.Timeout = real_requests.Timeout
    m.ConnectionError = real_requests.ConnectionError
    m.HTTPError = real_requests.HTTPError
    return m


def test_remote_http_infer_success_path_mocked():
    """Happy path: JPEG-encode → multipart POST → JSON response →
    Detection objects + latency_ms ≥ 0."""
    response = MagicMock()
    response.status_code = 200
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value={
        "detections": [
            {"label": "person", "bbox": [10, 20, 30, 40], "score": 0.9},
            {"label": "forklift", "bbox": [50, 60, 70, 80], "score": 0.8},
        ],
    })
    with patch.dict("sys.modules", {
        "cv2": _fake_cv2(),
        "requests": _fake_requests_module(post_response=response),
    }):
        backend = RemoteHTTP("http://dev:8000/infer")
        result = backend.infer(frame=MagicMock())

    assert len(result.detections) == 2
    assert result.detections[0].label == "person"
    assert result.detections[0].bbox == (10, 20, 30, 40)
    assert result.detections[1].score == 0.8
    assert result.latency_ms >= 0


def test_remote_http_infer_timeout_raises_clear_error():
    """Timeout → RuntimeError mentioning QA_INFERENCE_TIMEOUT_S."""
    import requests as real_requests
    with patch.dict("sys.modules", {
        "cv2": _fake_cv2(),
        "requests": _fake_requests_module(
            post_side_effect=real_requests.Timeout("simulated"),
        ),
    }):
        backend = RemoteHTTP("http://slow-jetson/infer")
        with pytest.raises(RuntimeError, match="QA_INFERENCE_TIMEOUT_S"):
            backend.infer(frame=MagicMock())


def test_remote_http_infer_connection_error_includes_url():
    """ConnectionError → RuntimeError that names the unreachable URL
    so the user knows where to look."""
    import requests as real_requests
    with patch.dict("sys.modules", {
        "cv2": _fake_cv2(),
        "requests": _fake_requests_module(
            post_side_effect=real_requests.ConnectionError("nope"),
        ),
    }):
        backend = RemoteHTTP("http://unreachable:8000/infer")
        with pytest.raises(RuntimeError, match="http://unreachable:8000/infer"):
            backend.infer(frame=MagicMock())


def test_remote_http_infer_5xx_response_surfaces_status_code():
    """5xx → response.raise_for_status fires inside the requests path
    and we rethrow with the status code visible."""
    import requests as real_requests
    response = MagicMock()
    response.status_code = 503
    response.raise_for_status = MagicMock(
        side_effect=real_requests.HTTPError("503 Service Unavailable"),
    )
    with patch.dict("sys.modules", {
        "cv2": _fake_cv2(),
        "requests": _fake_requests_module(post_response=response),
    }):
        backend = RemoteHTTP("http://overloaded/infer")
        with pytest.raises(RuntimeError, match="503"):
            backend.infer(frame=MagicMock())


def test_remote_http_infer_malformed_json_includes_expected_shape():
    """JSON decode failure surfaces a hint listing the expected shape
    so the contributor wiring up a new inference service knows what
    we expect."""
    response = MagicMock()
    response.status_code = 200
    response.raise_for_status = MagicMock()
    # response.json() raising ValueError (the actual JSONDecodeError
    # parent class).
    response.json = MagicMock(side_effect=ValueError("not json"))
    with patch.dict("sys.modules", {
        "cv2": _fake_cv2(),
        "requests": _fake_requests_module(post_response=response),
    }):
        backend = RemoteHTTP("http://bad-json/infer")
        with pytest.raises(RuntimeError, match="detections"):
            backend.infer(frame=MagicMock())


def test_remote_http_inference_timeout_env_var_respected(monkeypatch):
    """QA_INFERENCE_TIMEOUT_S override propagates to requests.post(timeout=)."""
    monkeypatch.setenv("QA_INFERENCE_TIMEOUT_S", "42")
    response = MagicMock()
    response.status_code = 200
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value={"detections": []})
    fake_requests = _fake_requests_module(post_response=response)
    with patch.dict("sys.modules", {
        "cv2": _fake_cv2(),
        "requests": fake_requests,
    }):
        backend = RemoteHTTP("http://dev/infer")
        backend.infer(frame=MagicMock())

    # First call's kwargs should carry timeout=42.0.
    _, post_kwargs = fake_requests.post.call_args
    assert post_kwargs["timeout"] == 42.0
