"""v1.1.0 PR-1 — ground_truth.load_annotations."""
from __future__ import annotations

import json

import pytest

from mk_qa_master.edge.ground_truth import load_annotations


def test_load_annotations_normalizes_frame_keys_to_int(tmp_path):
    """JSON keys are strings; the loader returns int-keyed dicts so
    callers can do `frames.get(frame_idx, [])` against runtime ints."""
    path = tmp_path / "ann.json"
    path.write_text(json.dumps({
        "fps": 30,
        "frames": {
            "0": [{"label": "person", "bbox": [0, 0, 10, 10]}],
            "45": [{"label": "forklift", "bbox": [50, 50, 30, 30]}],
        },
    }))
    frames, fps = load_annotations(path)
    assert fps == 30.0
    assert 0 in frames
    assert 45 in frames
    assert all(isinstance(k, int) for k in frames.keys())


def test_load_annotations_default_fps_30(tmp_path):
    """When `fps` is omitted, default to 30.0 — matches the common
    fixture format."""
    path = tmp_path / "ann.json"
    path.write_text(json.dumps({"frames": {}}))
    _, fps = load_annotations(path)
    assert fps == 30.0


def test_load_annotations_explicit_fps_overrides(tmp_path):
    """Custom fps field passes through."""
    path = tmp_path / "ann.json"
    path.write_text(json.dumps({"fps": 60, "frames": {}}))
    _, fps = load_annotations(path)
    assert fps == 60.0


def test_load_annotations_missing_file_raises(tmp_path):
    """File not found → FileNotFoundError. Callers (e.g., the
    generated test) decide whether to skip or fail."""
    with pytest.raises(FileNotFoundError):
        load_annotations(tmp_path / "missing.json")


def test_load_annotations_malformed_json_raises(tmp_path):
    """Bad JSON → JSONDecodeError. Loader doesn't try to recover."""
    path = tmp_path / "bad.json"
    path.write_text("{not valid json")
    with pytest.raises(json.JSONDecodeError):
        load_annotations(path)


def test_load_annotations_empty_frames_returns_empty_dict(tmp_path):
    """`frames: {}` is valid — caller's loop just iterates over nothing."""
    path = tmp_path / "ann.json"
    path.write_text(json.dumps({"frames": {}}))
    frames, _ = load_annotations(path)
    assert frames == {}
