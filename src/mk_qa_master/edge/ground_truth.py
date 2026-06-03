"""Annotations sidecar loader for Edge runner generated tests.

Each test fixture (e.g., `fixtures/factory.mp4`) gets a JSON sidecar
listing the expected detections per frame:

    {
      "fps": 30,
      "frames": {
        "0":  [{"label": "person",   "bbox": [120, 80, 60, 160]}],
        "45": [{"label": "forklift", "bbox": [300, 200, 180, 120]}]
      }
    }

The generated `test_detect_<label>` functions iterate frames, ask the
model for detections, and use `metrics.match_detection` to check each
expected entry.

This module is intentionally tiny — `cv2` / `ultralytics` only get
imported by the modules that actually run inference. Tests can mock
the file path or pass an in-memory dict.
"""
from __future__ import annotations

import json
from pathlib import Path


def load_annotations(path: str | Path) -> tuple[dict[int, list[dict]], float]:
    """Read an annotations JSON file. Returns (frames, fps).

    `frames` is `{frame_index: [{"label", "bbox"}, ...]}`. Frame indices
    are coerced to int so callers can do `frames.get(frame_idx, [])`
    against the runtime frame counter.

    `fps` defaults to 30.0 when omitted from the file — matches the
    most common fixture format. Pass an explicit `fps` field in the
    JSON to override.

    Raises `FileNotFoundError` / `json.JSONDecodeError` on bad inputs.
    The runner expects sidecar files to exist when callers request
    them; the generated tests can opt-out by passing `annotations_path=""`.
    """
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    raw_frames = data.get("frames") or {}
    frames: dict[int, list[dict]] = {
        int(k): list(v) for k, v in raw_frames.items()
    }
    fps = float(data.get("fps", 30.0))
    return frames, fps
