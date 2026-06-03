"""v1.1.0 PR-1 — metrics.py: IoU, match_detection, LatencyTracker."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from mk_qa_master.edge.metrics import iou, match_detection, LatencyTracker


@dataclass
class _Pred:
    """Stand-in for inference.Detection — same .label / .bbox attrs."""
    label: str
    bbox: tuple


# ---- iou -----------------------------------------------------------------

def test_iou_perfect_overlap():
    """Two identical boxes → IoU = 1.0."""
    a = (10, 10, 50, 50)
    assert iou(a, a) == pytest.approx(1.0)


def test_iou_no_overlap():
    """Disjoint boxes → IoU = 0.0."""
    a = (0, 0, 10, 10)
    b = (100, 100, 10, 10)
    assert iou(a, b) == 0.0


def test_iou_partial_overlap():
    """Half-overlap on x-axis → intersection 5×10 = 50,
       union 100 + 100 − 50 = 150 → 50/150 = 0.333..."""
    a = (0, 0, 10, 10)
    b = (5, 0, 10, 10)
    assert iou(a, b) == pytest.approx(50 / 150)


def test_iou_zero_size_box_returns_zero():
    """A zero-area box returns 0.0 not NaN/Infinity."""
    assert iou((0, 0, 0, 0), (0, 0, 10, 10)) == 0.0


# ---- match_detection -----------------------------------------------------

def test_match_detection_label_mismatch():
    """A high-IoU prediction with the wrong label doesn't match."""
    expected = {"label": "person", "bbox": [10, 10, 50, 50]}
    preds = [_Pred("forklift", (10, 10, 50, 50))]  # perfect IoU, wrong label
    assert match_detection(preds, expected, 0.5) is False


def test_match_detection_iou_below_threshold():
    """Same label but IoU below threshold → no match."""
    expected = {"label": "person", "bbox": [0, 0, 10, 10]}
    preds = [_Pred("person", (5, 0, 10, 10))]  # IoU = 0.333
    assert match_detection(preds, expected, 0.5) is False


def test_match_detection_iou_at_threshold_counts():
    """IoU == threshold counts as a match (≥ comparison)."""
    expected = {"label": "person", "bbox": [0, 0, 10, 10]}
    # Construct boxes so IoU is exactly 0.5: each 10x10, union 150,
    # intersection 50 → 1/3 → not 0.5. Use 0.4 as threshold instead.
    preds = [_Pred("person", (5, 0, 10, 10))]  # IoU = 1/3
    assert match_detection(preds, expected, 1 / 3) is True


def test_match_detection_picks_first_qualifying_among_many():
    """Multiple predictions; one matches → True."""
    expected = {"label": "person", "bbox": [10, 10, 50, 50]}
    preds = [
        _Pred("forklift", (10, 10, 50, 50)),  # wrong label
        _Pred("person",   (10, 10, 50, 50)),  # match
        _Pred("person",   (200, 200, 50, 50)),  # IoU=0, ignored
    ]
    assert match_detection(preds, expected, 0.5) is True


# ---- LatencyTracker -------------------------------------------------------

def test_latency_tracker_empty_returns_zero():
    """No samples → p95/mean/max all return 0.0 (no division-by-zero)."""
    t = LatencyTracker()
    assert t.p95() == 0.0
    assert t.mean() == 0.0
    assert t.max() == 0.0


def test_latency_tracker_p95_with_small_n():
    """20 samples — p95 is index int(20 * 0.95) = 19, which is the
    last after sort (samples 0..19 → max 19 ms in this fixture)."""
    t = LatencyTracker()
    for ms in range(20):
        t.add(float(ms))
    assert t.p95() == 19.0


def test_latency_tracker_p95_with_realistic_inference_set():
    """50 samples spread 10..60 ms — p95 is the 47th (int(50 * 0.95))
    after sort. Locks the rank semantics."""
    t = LatencyTracker()
    for ms in range(10, 60):
        t.add(float(ms))
    # index 47 after sort = 47 + 10 = 57.0
    assert t.p95() == 57.0


def test_latency_tracker_mean_and_max():
    """Sanity: mean = arithmetic average, max = top sample."""
    t = LatencyTracker()
    for ms in (10.0, 20.0, 30.0, 40.0, 50.0):
        t.add(ms)
    assert t.mean() == 30.0
    assert t.max() == 50.0
