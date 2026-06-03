"""Detection-quality + latency metrics for Edge runner generated tests.

Three pieces:

  - `iou(box_a, box_b)` — standard intersection-over-union for two
    axis-aligned bounding boxes in (x, y, w, h) form. Returns 0.0
    rather than dividing by zero when the union is empty.

  - `match_detection(predictions, expected, iou_threshold)` — given a
    list of model outputs and a single expected detection (label +
    bbox), True iff any prediction with the same label has IoU above
    threshold. Used by generated tests to assert "the model found
    what was in the ground truth at frame N".

  - `LatencyTracker` — accumulates per-frame latencies and exposes
    p95 / mean / max. Sized for the SLA assertions generated tests
    emit (e.g., "p95 ≤ 40 ms").

No external deps (no numpy, no opencv) — keeps base install light.
"""
from __future__ import annotations

from typing import Any


def iou(box_a: tuple[float, float, float, float],
        box_b: tuple[float, float, float, float]) -> float:
    """Intersection-over-union for two (x, y, w, h) boxes.

    Returns a value in [0.0, 1.0]. Zero-sized boxes or non-overlapping
    boxes return 0.0 (never raises; this is called per-frame from
    generated tests and we'd rather a missed detection than a crash).
    """
    ax, ay, aw, ah = box_a
    bx, by, bw, bh = box_b
    ix1, iy1 = max(ax, bx), max(ay, by)
    ix2, iy2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def match_detection(predictions: list[Any],
                    expected: dict,
                    iou_threshold: float) -> bool:
    """True iff any prediction with the same label as `expected` has
    IoU ≥ `iou_threshold` against `expected["bbox"]`.

    `predictions` items must have `.label` and `.bbox` attributes
    (Detection-shaped). `expected` is `{"label": str, "bbox": [x,y,w,h]}`.

    Generated tests use this to assert "the model detected what was
    annotated at frame N" without coupling to a specific prediction
    ordering or count.
    """
    target_label = expected["label"]
    target_box = tuple(expected["bbox"])
    return any(
        getattr(pred, "label", None) == target_label
        and iou(getattr(pred, "bbox", (0, 0, 0, 0)), target_box) >= iou_threshold
        for pred in predictions
    )


class LatencyTracker:
    """Accumulates per-frame inference latency samples and exposes
    aggregates for SLA assertions.

    Designed for the generated test pattern:
        latency.add(res.latency_ms)
        assert latency.p95() <= SLA
    """

    def __init__(self) -> None:
        self.samples: list[float] = []

    def add(self, ms: float) -> None:
        self.samples.append(float(ms))

    def p95(self) -> float:
        """95th percentile. Empty tracker returns 0.0 so a test that
        ran zero frames doesn't false-alarm on latency."""
        if not self.samples:
            return 0.0
        sorted_samples = sorted(self.samples)
        # `min` clamps the index for tiny sample sizes — e.g. 3 samples
        # gives index = int(3*0.95) = 2, which is the last element;
        # avoids IndexError when len < 20.
        idx = min(len(sorted_samples) - 1, int(len(sorted_samples) * 0.95))
        return sorted_samples[idx]

    def mean(self) -> float:
        if not self.samples:
            return 0.0
        return sum(self.samples) / len(self.samples)

    def max(self) -> float:
        if not self.samples:
            return 0.0
        return max(self.samples)
