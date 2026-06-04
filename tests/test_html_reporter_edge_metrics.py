"""v1.3.1 — HTML reporter renders the v1.3.0 `edge_metrics` block.

The bugfix that motivated this file is the edge runner missing
`get_all_test_details()` (covered in tests/test_edge_runner.py). The
second half of the fix is the reporter actually rendering the
`edge_metrics` block surfaced by that override; without it, cards
would render but Edge-specific data (p95 latency / fps / labels)
would still be invisible.

These tests pin the renderer in isolation so a future PR that
reshuffles the HTML template can't silently drop the block.
"""
from __future__ import annotations

import pytest

from mk_qa_master.reporters.html import _render_edge_metrics_html


def test_render_edge_metrics_returns_empty_string_when_block_missing():
    """Non-edge runners pay zero HTML cost — the helper must short-circuit
    on None / empty dict / non-dict inputs."""
    assert _render_edge_metrics_html(None) == ""
    assert _render_edge_metrics_html({}) == ""
    assert _render_edge_metrics_html("not a dict") == ""  # type: ignore[arg-type]


def test_render_edge_metrics_includes_p95_latency_and_fps():
    em = {"p95_latency_ms": 38.5, "fps": 27.3}
    html = _render_edge_metrics_html(em)
    assert 'class="edge-metrics"' in html
    assert "p95 latency" in html
    assert "38.5 ms" in html
    assert "27.3" in html


def test_render_edge_metrics_summarizes_iou_per_frame_avg_and_count():
    """`iou_per_frame` is a list of floats — the card surfaces the
    average + sample count rather than dumping the whole list."""
    em = {"iou_per_frame": [0.71, 0.74, 0.68]}
    html = _render_edge_metrics_html(em)
    assert "iou (avg / n)" in html
    # Avg = 0.71 (rounded to 2dp)
    assert "0.71" in html
    assert "/ 3" in html


def test_render_edge_metrics_escapes_label_names():
    """Labels come from user-supplied annotations — must HTML-escape to
    prevent injection if someone names a label `<script>`."""
    em = {"labels_covered": ["forklift", "<script>"]}
    html = _render_edge_metrics_html(em)
    assert "&lt;script&gt;" in html
    assert "<script>" not in html


def test_render_edge_metrics_ignores_unknown_fields():
    """Only the four documented fields render today; an additive future
    field (e.g. `corrupted_frame_rate`) is silently ignored until
    intentionally surfaced. Prevents accidental leakage of internal
    metric names into the user-facing report."""
    em = {"future_metric": 42}
    assert _render_edge_metrics_html(em) == ""
