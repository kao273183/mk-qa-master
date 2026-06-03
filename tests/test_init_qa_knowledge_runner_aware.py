"""v1.2.0 PR-4 — init_qa_knowledge runner-aware Edge section selection.

Closes v1.1 postmortem §9 #4. When QA_RUNNER=edge (or its rtsp alias),
the scaffolded qa-knowledge.md's response gains
`runner_section_included: true` so host LLMs know to point the user
at the bundled "Edge Vision Inference Testing" section first.

This is a documentation-discoverability hint; the section itself is
already in the bundled methodology (v1.1.1 added it). Surface change
triggers the v1.0 schema-snapshot ack — paired with the MIGRATION-1.x
v1.1.2 → v1.2.0 entry in the same PR.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest


@pytest.fixture
def isolated_knowledge_path(tmp_path, monkeypatch):
    """Reload config + qa_context with QA_KNOWLEDGE_FILE pointing at
    tmp — keeps tests from clobbering the real project knowledge file."""
    knowledge_file = tmp_path / "qa-knowledge.md"
    monkeypatch.setenv("QA_KNOWLEDGE_FILE", str(knowledge_file))
    import mk_qa_master.config as cfg
    importlib.reload(cfg)
    from mk_qa_master.tools import qa_context
    importlib.reload(qa_context)
    return knowledge_file, qa_context


# ---- Default runner: no Edge section flag --------------------------------

def test_init_does_not_set_runner_section_flag_for_default_runner(
    monkeypatch, isolated_knowledge_path
):
    """QA_RUNNER unset / default pytest → no runner-specific Edge hint."""
    monkeypatch.delenv("QA_RUNNER", raising=False)
    _, qa_context = isolated_knowledge_path
    out = qa_context.init_qa_knowledge()
    assert out["created"] is True
    assert out["runner_section_included"] is False


def test_init_does_not_set_runner_section_flag_for_pytest_runner(
    monkeypatch, isolated_knowledge_path
):
    """Explicit QA_RUNNER=pytest → still no Edge hint (Edge runner only)."""
    monkeypatch.setenv("QA_RUNNER", "pytest")
    _, qa_context = isolated_knowledge_path
    out = qa_context.init_qa_knowledge()
    assert out["runner_section_included"] is False


# ---- Edge runner: hint surfaces -------------------------------------------

def test_init_sets_runner_section_flag_when_runner_is_edge(
    monkeypatch, isolated_knowledge_path
):
    """QA_RUNNER=edge → response signals the Edge section is included.
    The bundled methodology always contains it (v1.1.1); the flag is the
    discoverability signal for host LLMs."""
    monkeypatch.setenv("QA_RUNNER", "edge")
    _, qa_context = isolated_knowledge_path
    out = qa_context.init_qa_knowledge()
    assert out["created"] is True
    assert out["runner_section_included"] is True


def test_init_sets_runner_section_flag_for_rtsp_alias(
    monkeypatch, isolated_knowledge_path
):
    """rtsp alias points at EdgeInferenceRunner; same behavior expected."""
    monkeypatch.setenv("QA_RUNNER", "rtsp")
    _, qa_context = isolated_knowledge_path
    out = qa_context.init_qa_knowledge()
    assert out["runner_section_included"] is True


def test_edge_next_step_mentions_edge_section_in_english(
    monkeypatch, isolated_knowledge_path
):
    """When QA_RUNNER=edge and QA_LANG=en, the next_step hint points at
    the Edge Vision Inference Testing section explicitly."""
    monkeypatch.setenv("QA_RUNNER", "edge")
    monkeypatch.setenv("QA_LANG", "en")
    _, qa_context = isolated_knowledge_path
    out = qa_context.init_qa_knowledge()
    assert "Edge Vision Inference" in out["next_step"]


def test_edge_next_step_mentions_edge_section_in_zh_tw(
    monkeypatch, tmp_path,
):
    """zh-tw: 邊緣視覺推論測試 — same hint, translated.

    Reloads config + qa_context with QA_LANG=zh-tw set BEFORE the
    reload happens; both modules cache QA_LANG at import time, so the
    standard isolated_knowledge_path fixture (which reloads with
    QA_LANG=en) wouldn't reflect a later monkeypatch.
    """
    knowledge_file = tmp_path / "qa-knowledge-zh.md"
    monkeypatch.setenv("QA_KNOWLEDGE_FILE", str(knowledge_file))
    monkeypatch.setenv("QA_LANG", "zh-tw")
    monkeypatch.setenv("QA_RUNNER", "edge")
    import mk_qa_master.config as cfg
    importlib.reload(cfg)
    from mk_qa_master.tools import qa_context
    importlib.reload(qa_context)

    out = qa_context.init_qa_knowledge()
    assert "邊緣視覺推論" in out["next_step"]


# ---- Existing-file path also carries the flag ----------------------------

def test_init_carries_runner_section_flag_when_file_exists(
    monkeypatch, isolated_knowledge_path
):
    """Idempotent path (file exists, no overwrite) — response must STILL
    carry the runner_section_included field so host LLMs can use it for
    discoverability even when no file was newly written."""
    monkeypatch.setenv("QA_RUNNER", "edge")
    knowledge_file, qa_context = isolated_knowledge_path
    # Materialize a pre-existing file.
    knowledge_file.write_text("existing user content", encoding="utf-8")

    out = qa_context.init_qa_knowledge()
    assert out["created"] is False
    assert "existing_bytes" in out
    assert out["runner_section_included"] is True
