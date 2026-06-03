"""v1.1.0 PR-3 — analyze_stream MCP tool.

cv2 + the stream itself are mocked — tests verify the contract
(vendor blacklist, missing-extras envelope, candidate_tcs shape,
annotations-driven label discovery) without actually probing a
real RTSP source.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from mk_qa_master.tools.analyze_stream import analyze_stream


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("QA_EDGE_ALLOW_VENDOR_HOSTS", raising=False)
    yield


# ---- bad_request ---------------------------------------------------------

def test_missing_rtsp_url_returns_bad_request():
    """rtsp_url is required."""
    out = analyze_stream({})
    assert out["error"] == "bad_request"
    assert "rtsp_url" in out["hint"]


def test_empty_rtsp_url_returns_bad_request():
    """Whitespace-only URL counts as missing."""
    out = analyze_stream({"rtsp_url": "   "})
    assert out["error"] == "bad_request"


# ---- vendor-host blacklist (§11 #6) -------------------------------------

def test_dahua_host_refused_by_default():
    out = analyze_stream({"rtsp_url": "rtsp://camera.dahua.com:554/stream"})
    assert out["error"] == "forbidden_vendor_host"
    assert out["blocked_host"] == "camera.dahua.com"
    assert "QA_EDGE_ALLOW_VENDOR_HOSTS" in out["hint"]


def test_hikvision_host_refused_by_default():
    out = analyze_stream({"rtsp_url": "rtsp://cam.hikvision.com/live"})
    assert out["error"] == "forbidden_vendor_host"


def test_vendor_host_override_allows_request_through(monkeypatch):
    """Set the override → host check passes → tool proceeds to cv2
    (which we mock to return a successful probe)."""
    monkeypatch.setenv("QA_EDGE_ALLOW_VENDOR_HOSTS", "true")
    fake_cap = MagicMock()
    fake_cap.isOpened.return_value = True
    fake_cap.get.side_effect = lambda prop: {
        # cv2.CAP_PROP_FRAME_WIDTH = 3
        # cv2.CAP_PROP_FRAME_HEIGHT = 4
        # cv2.CAP_PROP_FPS = 5
        3: 1280, 4: 720, 5: 30.0,
    }.get(prop, 0)

    fake_cv2 = MagicMock(
        VideoCapture=MagicMock(return_value=fake_cap),
        CAP_PROP_FRAME_WIDTH=3,
        CAP_PROP_FRAME_HEIGHT=4,
        CAP_PROP_FPS=5,
    )
    with patch.dict("sys.modules", {"cv2": fake_cv2}):
        out = analyze_stream({"rtsp_url": "rtsp://cam.dahua.com/stream"})
    assert "error" not in out
    assert out["url"] == "rtsp://cam.dahua.com/stream"
    assert out["width"] == 1280


def test_non_vendor_host_passes_blacklist(monkeypatch):
    """Custom domains (not in the blacklist) sail past the check."""
    fake_cap = MagicMock()
    fake_cap.isOpened.return_value = True
    fake_cap.get.side_effect = lambda prop: {3: 640, 4: 480, 5: 25.0}.get(prop, 0)
    fake_cv2 = MagicMock(
        VideoCapture=MagicMock(return_value=fake_cap),
        CAP_PROP_FRAME_WIDTH=3,
        CAP_PROP_FRAME_HEIGHT=4,
        CAP_PROP_FPS=5,
    )
    with patch.dict("sys.modules", {"cv2": fake_cv2}):
        out = analyze_stream({"rtsp_url": "rtsp://mycam.staging.example.com/feed"})
    assert "error" not in out


# ---- missing_extras envelope --------------------------------------------

def test_missing_cv2_returns_missing_extras_envelope():
    """When cv2 isn't installed (e.g., base install without [edge]),
    return a clear error envelope rather than crashing."""
    # Patch the import to raise; the tool catches ImportError and
    # surfaces the missing_extras envelope.
    with patch.dict("sys.modules", {"cv2": None}):
        # patch.dict with None makes `import cv2` raise ImportError
        out = analyze_stream({"rtsp_url": "rtsp://test/stream"})
    assert out["error"] == "missing_extras"
    assert "mk-qa-master[edge]" in out["hint"]


# ---- stream_unreachable -------------------------------------------------

def test_stream_unreachable_when_cv2_cannot_open():
    """cv2.VideoCapture('rtsp://...').isOpened() returning False
    surfaces as stream_unreachable."""
    fake_cap = MagicMock()
    fake_cap.isOpened.return_value = False
    fake_cv2 = MagicMock(
        VideoCapture=MagicMock(return_value=fake_cap),
        CAP_PROP_FRAME_WIDTH=3,
        CAP_PROP_FRAME_HEIGHT=4,
        CAP_PROP_FPS=5,
    )
    with patch.dict("sys.modules", {"cv2": fake_cv2}):
        out = analyze_stream({"rtsp_url": "rtsp://unreachable/stream"})
    assert out["error"] == "stream_unreachable"
    assert out["url"] == "rtsp://unreachable/stream"


# ---- happy path ---------------------------------------------------------

def _mock_cv2_with_capture(width: int, height: int, fps: float):
    fake_cap = MagicMock()
    fake_cap.isOpened.return_value = True
    fake_cap.get.side_effect = lambda prop: {
        3: width, 4: height, 5: fps,
    }.get(prop, 0)
    return MagicMock(
        VideoCapture=MagicMock(return_value=fake_cap),
        CAP_PROP_FRAME_WIDTH=3,
        CAP_PROP_FRAME_HEIGHT=4,
        CAP_PROP_FPS=5,
    )


def test_basic_metadata_without_annotations():
    """No annotations_path → labels=[], candidate_tcs has only the
    four runner-standard entries (throughput / latency / reconnect /
    empty-frame)."""
    with patch.dict("sys.modules",
                    {"cv2": _mock_cv2_with_capture(1920, 1080, 30.0)}):
        out = analyze_stream({"rtsp_url": "rtsp://test/stream"})
    assert out["width"] == 1920
    assert out["height"] == 1080
    assert out["fps"] == 30.0
    assert out["labels"] == []
    # Four runner-standard candidate TCs, no per-label entries.
    assert len(out["candidate_tcs"]) == 4
    joined = " ".join(out["candidate_tcs"]).lower()
    assert "throughput" in joined
    assert "latency" in joined or "sla" in joined
    assert "reconnect" in joined or "interrupt" in joined
    assert "empty" in joined or "false-positive" in joined


def test_annotations_drive_per_label_candidate_tcs(tmp_path):
    """When annotations_path resolves, labels become the seed for
    per-label TCs (strings — schema parity with analyze_url per §11 #5)."""
    ann = tmp_path / "ann.json"
    ann.write_text(json.dumps({
        "fps": 30,
        "frames": {
            "0":  [{"label": "person",  "bbox": [0, 0, 10, 10]}],
            "20": [{"label": "forklift", "bbox": [50, 50, 30, 30]}],
            "45": [{"label": "person",  "bbox": [60, 60, 10, 10]}],
        },
    }))

    with patch.dict("sys.modules",
                    {"cv2": _mock_cv2_with_capture(1280, 720, 30.0)}):
        out = analyze_stream({
            "rtsp_url": "rtsp://test/stream",
            "annotations_path": str(ann),
        })

    # Labels are sorted, deduped.
    assert out["labels"] == ["forklift", "person"]
    # Two per-label TCs + four runner-standard = 6 total.
    assert len(out["candidate_tcs"]) == 6
    # Per-label TCs come first and are strings.
    per_label = out["candidate_tcs"][:2]
    assert all(isinstance(s, str) for s in per_label)
    assert any("person" in s for s in per_label)
    assert any("forklift" in s for s in per_label)


def test_missing_annotations_file_is_non_fatal(tmp_path):
    """A missing annotations file falls back to label-free TCs rather
    than erroring out — analyze_stream's primary job is the geometry
    probe."""
    with patch.dict("sys.modules",
                    {"cv2": _mock_cv2_with_capture(640, 480, 25.0)}):
        out = analyze_stream({
            "rtsp_url": "rtsp://test/stream",
            "annotations_path": str(tmp_path / "does-not-exist.json"),
        })
    assert "error" not in out
    assert out["labels"] == []
    assert len(out["candidate_tcs"]) == 4
