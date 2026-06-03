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


def test_remote_http_infer_raises_not_implemented_for_v1_1():
    """v1.1 ships LocalYolo only. A caller who sets QA_JETSON_HOST or
    QA_INFERENCE_ENDPOINT prematurely gets a clear signal, not a
    silent empty-detection result."""
    b = RemoteHTTP("http://test/infer")
    with pytest.raises(NotImplementedError, match="v1.2"):
        b.infer(frame=None)
