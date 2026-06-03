"""v1.3.0 PR-2 — Edge AI runner flake signals in get_optimization_plan.

Synthesizes history-shaped dicts in-memory and runs them through
_analyze_edge_signals + _prioritize. No file IO; tests live entirely
in-process.

The 4 signals (per docs/prd-v1.3-edge-ai-phase-4.md §4):
  - latency_p95_exceeded_sla — 🔴 high, current run only
  - fps_variance_across_runs — 🟡 medium, ≥ 5 runs window
  - iou_jitter_per_tc        — 🟡 medium, stddev > 0.1
  - coverage_gap_per_label   — 🟡 medium, label in metrics but no nodeid
"""
from __future__ import annotations

import pytest

from mk_qa_master.tools.optimizer import (
    _analyze_edge_signals,
    _prioritize,
    build_plan,
)


def _make_history(
    *test_entries_per_run: list[dict],
) -> list[dict]:
    """Wrap N runs (each a list of test entries) into the optimizer's
    history shape: [{file, data: {tests: [...]}}, ...]."""
    return [
        {"file": f"run-{i}.json", "data": {"tests": list(entries)}}
        for i, entries in enumerate(test_entries_per_run)
    ]


def _ok_test(nodeid: str, edge_metrics: dict | None = None) -> dict:
    """Build a passing test entry with optional edge_metrics block."""
    t = {
        "nodeid": nodeid,
        "outcome": "passed",
        "call": {"duration": 0.5},
    }
    if edge_metrics is not None:
        t["edge_metrics"] = edge_metrics
    return t


# ---- latency_p95_exceeded_sla -------------------------------------------


def test_latency_signal_fires_when_p95_exceeds_default_sla(monkeypatch):
    """Default SLA is 40ms — a single run with p95=60 trips it."""
    monkeypatch.delenv("EDGE_LATENCY_SLA_MS", raising=False)
    history = _make_history([
        _ok_test("tests/test_edge.py::test_detect_person",
                 edge_metrics={"p95_latency_ms": 60.0, "fps": 28.0}),
    ])
    signals = _analyze_edge_signals(history)
    assert len(signals["latency_sla_breaches"]) == 1
    breach = signals["latency_sla_breaches"][0]
    assert breach["nodeid"] == "tests/test_edge.py::test_detect_person"
    assert breach["p95_latency_ms"] == 60.0
    assert breach["sla_ms"] == 40.0


def test_latency_signal_respects_env_override(monkeypatch):
    """EDGE_LATENCY_SLA_MS=16 (60fps target) → 60ms p95 is a 4x breach."""
    monkeypatch.setenv("EDGE_LATENCY_SLA_MS", "16")
    history = _make_history([
        _ok_test("t1", edge_metrics={"p95_latency_ms": 60.0}),
    ])
    signals = _analyze_edge_signals(history)
    breaches = signals["latency_sla_breaches"]
    assert len(breaches) == 1
    assert breaches[0]["sla_ms"] == 16.0


def test_latency_signal_uses_latest_run_only():
    """If first 4 runs were 60ms but the latest is 20ms (under SLA),
    no breach should fire — we only care about current behavior."""
    history = _make_history(
        *[
            [_ok_test("t1", edge_metrics={"p95_latency_ms": 60.0})]
            for _ in range(4)
        ],
        [_ok_test("t1", edge_metrics={"p95_latency_ms": 20.0})],
    )
    signals = _analyze_edge_signals(history)
    assert signals["latency_sla_breaches"] == []


# ---- fps_variance_across_runs -------------------------------------------


def test_fps_variance_signal_fires_above_20_percent_relative_stddev():
    """Build 5 runs with FPS [15, 30, 20, 35, 18] — relative stddev > 30%
    → signal fires."""
    history = _make_history(*[
        [_ok_test("t1", edge_metrics={"fps": fps, "p95_latency_ms": 10.0})]
        for fps in [15.0, 30.0, 20.0, 35.0, 18.0]
    ])
    signals = _analyze_edge_signals(history)
    variances = signals["fps_variance"]
    assert len(variances) == 1
    assert variances[0]["nodeid"] == "t1"
    assert variances[0]["relative_stddev"] > 0.2


def test_fps_variance_signal_quiet_below_threshold():
    """Tight cluster around 25 (24/25/26/25/24) → < 5% stddev → no signal."""
    history = _make_history(*[
        [_ok_test("t1", edge_metrics={"fps": fps, "p95_latency_ms": 10.0})]
        for fps in [24.0, 25.0, 26.0, 25.0, 24.0]
    ])
    signals = _analyze_edge_signals(history)
    assert signals["fps_variance"] == []


def test_fps_variance_needs_at_least_5_runs():
    """4 runs of bouncing FPS — not enough data; signal stays quiet
    (prevents false positives from cold-start noise)."""
    history = _make_history(*[
        [_ok_test("t1", edge_metrics={"fps": fps, "p95_latency_ms": 10.0})]
        for fps in [15.0, 35.0, 18.0, 32.0]  # only 4 runs
    ])
    signals = _analyze_edge_signals(history)
    assert signals["fps_variance"] == []


# ---- iou_jitter_per_tc --------------------------------------------------


def test_iou_jitter_signal_fires_above_0_1_stddev():
    """IoU samples [0.4, 0.9, 0.5, 0.8, 0.45, 0.85] — stddev > 0.2 → signal."""
    history = _make_history([
        _ok_test("t1", edge_metrics={
            "p95_latency_ms": 10.0, "fps": 25.0,
            "iou_per_frame": [0.4, 0.9, 0.5, 0.8, 0.45, 0.85],
        }),
    ])
    signals = _analyze_edge_signals(history)
    jitters = signals["iou_jitter"]
    assert len(jitters) == 1
    assert jitters[0]["nodeid"] == "t1"
    assert jitters[0]["iou_stddev"] > 0.1


def test_iou_jitter_quiet_when_iou_stable():
    """All frames around 0.85±0.02 → stddev too small → no signal."""
    history = _make_history([
        _ok_test("t1", edge_metrics={
            "p95_latency_ms": 10.0, "fps": 25.0,
            "iou_per_frame": [0.85, 0.84, 0.86, 0.85, 0.85, 0.84],
        }),
    ])
    signals = _analyze_edge_signals(history)
    assert signals["iou_jitter"] == []


# ---- coverage_gap_per_label ---------------------------------------------


def test_coverage_gap_fires_when_label_not_in_any_nodeid():
    """edge_metrics.labels_covered has 'forklift' but no test_*_forklift
    nodeid exists → coverage gap."""
    history = _make_history([
        _ok_test("tests/edge.py::test_detect_person", edge_metrics={
            "p95_latency_ms": 10.0, "fps": 25.0,
            "labels_covered": ["person", "forklift"],
        }),
    ])
    signals = _analyze_edge_signals(history)
    gaps = signals["coverage_gaps"]
    assert len(gaps) == 1
    assert gaps[0]["label"] == "forklift"


def test_coverage_gap_quiet_when_all_labels_have_nodeids():
    """nodeids contain both 'person' AND 'forklift' substrings →
    no coverage gap."""
    history = _make_history([
        _ok_test("tests/edge.py::test_detect_person", edge_metrics={
            "p95_latency_ms": 10.0, "fps": 25.0,
            "labels_covered": ["person"],
        }),
        _ok_test("tests/edge.py::test_detect_forklift", edge_metrics={
            "p95_latency_ms": 10.0, "fps": 25.0,
            "labels_covered": ["forklift"],
        }),
    ])
    signals = _analyze_edge_signals(history)
    assert signals["coverage_gaps"] == []


# ---- baseline: signals empty when no edge_metrics anywhere --------------


def test_signals_empty_when_no_test_has_edge_metrics():
    """Plain pytest history (web/api tests, no edge runner) → no edge
    signals computed — backward compat for non-edge users."""
    history = _make_history([
        {"nodeid": "tests/web.py::test_login", "outcome": "passed",
         "call": {"duration": 0.4}},
    ])
    signals = _analyze_edge_signals(history)
    assert signals == {}


def test_signals_empty_when_history_empty():
    """No history yet — no signals."""
    assert _analyze_edge_signals([]) == {}


# ---- _prioritize integration --------------------------------------------


def test_prioritize_emits_high_priority_action_for_latency_breach(monkeypatch):
    """SLA breach → 🔴 high priority action with the SLA-tuning suggestion."""
    monkeypatch.delenv("EDGE_LATENCY_SLA_MS", raising=False)
    suite = {
        "tests": [],
        "edge_signals": {
            "latency_sla_breaches": [
                {"nodeid": "t1", "p95_latency_ms": 80.0, "sla_ms": 40.0},
            ],
            "fps_variance": [],
            "iou_jitter": [],
            "coverage_gaps": [],
        },
    }
    actions = _prioritize(suite, {"empty": True}, {"empty": True})
    breach_actions = [
        a for a in actions
        if a["category"] == "edge_latency_p95_exceeded_sla"
    ]
    assert len(breach_actions) == 1
    assert breach_actions[0]["priority"] == "high"
    assert breach_actions[0]["target"] == "t1"
    assert "SLA" in breach_actions[0]["evidence"].upper()


def test_prioritize_emits_medium_actions_for_variance_jitter_gaps():
    """The 3 medium-priority signals each surface as their own action."""
    suite = {
        "tests": [],
        "edge_signals": {
            "latency_sla_breaches": [],
            "fps_variance": [
                {"nodeid": "t1", "relative_stddev": 0.35,
                 "fps_window": [10, 30, 15, 28, 12]},
            ],
            "iou_jitter": [
                {"nodeid": "t2", "iou_stddev": 0.18, "sample_count": 30},
            ],
            "coverage_gaps": [
                {"label": "forklift", "evidence": "..."},
            ],
        },
    }
    actions = _prioritize(suite, {"empty": True}, {"empty": True})
    categories = [a["category"] for a in actions]
    assert "edge_fps_variance_across_runs" in categories
    assert "edge_iou_jitter_per_tc" in categories
    assert "edge_coverage_gap_per_label" in categories
    # All three are medium.
    edge_actions = [a for a in actions if a["category"].startswith("edge_")]
    assert all(a["priority"] == "medium" for a in edge_actions)


def test_prioritize_omits_edge_signals_when_suite_empty():
    """No history → suite={empty: True} → no edge actions."""
    actions = _prioritize({"empty": True}, {"empty": True}, {"empty": True})
    assert not any(a["category"].startswith("edge_") for a in actions)


# ---- build_plan end-to-end (smoke) --------------------------------------


def test_build_plan_includes_edge_signals_key_in_suite_quality():
    """The suite_quality block of build_plan() output contains the new
    edge_signals key — wires the surface change PR-4 will document in
    MIGRATION-1.x."""
    plan = build_plan(history_limit=10, telemetry_limit=50)
    suite = plan.get("suite_quality") or {}
    # Either suite is empty (no history) — fine — OR it contains the key.
    if not suite.get("empty"):
        assert "edge_signals" in suite
