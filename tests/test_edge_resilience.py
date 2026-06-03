"""v1.3.0 PR-1 — mk_qa_master.edge.resilience module.

Theme G Phase 4. subprocess.run + sys.platform mocked so tests never
shell out to a real `tc` or `ffmpeg` binary and pass on macOS / Windows
CI runners that don't have netem at all.

The threaded kill_ffmpeg_subprocess helper is verified with a small
real-time timeout (≤0.2s) so the test stays fast.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from mk_qa_master.edge.resilience import (
    apply_netem,
    build_corrupted_gop_fixture,
    clear_netem,
    kill_ffmpeg_subprocess,
)


@pytest.fixture
def _force_linux(monkeypatch):
    """Pretend we're on Linux so _linux_only() passes. macOS CI runs
    use this — the helpers themselves are env-mocked so no real `tc` /
    `ffmpeg` calls fire."""
    monkeypatch.setattr("mk_qa_master.edge.resilience.sys.platform", "linux")


@pytest.fixture
def _netem_enabled(monkeypatch):
    """Flip the consent gate on."""
    monkeypatch.setenv("QA_EDGE_NETEM_ENABLED", "true")


# ---- apply_netem ----------------------------------------------------------

def test_apply_netem_calls_tc_qdisc_add_with_correct_args(
    _force_linux, _netem_enabled
):
    """Default jitter=80ms / loss=2% propagates to the tc command."""
    with patch("subprocess.run") as mock_run:
        apply_netem()
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert cmd[:6] == ["tc", "qdisc", "add", "dev", "lo", "root"]
    assert "netem" in cmd
    assert "80ms" in cmd
    assert "2%" in cmd
    assert mock_run.call_args[1].get("check") is True


def test_apply_netem_respects_custom_jitter_and_loss(
    _force_linux, _netem_enabled
):
    """jitter_ms=200, loss_pct=10 propagate."""
    with patch("subprocess.run") as mock_run:
        apply_netem(jitter_ms=200, loss_pct=10)
    cmd = mock_run.call_args[0][0]
    assert "200ms" in cmd
    assert "10%" in cmd


def test_apply_netem_raises_when_QA_EDGE_NETEM_ENABLED_unset(_force_linux, monkeypatch):
    """Without the consent gate, the helper refuses to fire."""
    monkeypatch.delenv("QA_EDGE_NETEM_ENABLED", raising=False)
    with pytest.raises(RuntimeError, match="QA_EDGE_NETEM_ENABLED"):
        apply_netem()


def test_apply_netem_raises_on_non_linux(monkeypatch, _netem_enabled):
    """On macOS / Windows we don't even check the consent gate — the
    OS guard fires first because netem genuinely doesn't exist."""
    monkeypatch.setattr("mk_qa_master.edge.resilience.sys.platform", "darwin")
    with pytest.raises(RuntimeError, match="requires Linux"):
        apply_netem()


# ---- clear_netem ----------------------------------------------------------

def test_clear_netem_tears_down_quietly_when_nothing_set(_force_linux):
    """`tc qdisc del` returns non-zero when no qdisc exists; we run
    with check=False so teardown never crashes the fixture."""
    with patch("subprocess.run") as mock_run:
        clear_netem()
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert cmd == ["tc", "qdisc", "del", "dev", "lo", "root"]
    assert mock_run.call_args[1].get("check") is False


def test_clear_netem_requires_no_consent_gate(_force_linux, monkeypatch):
    """Clearing is always safe — no consent gate needed even when
    QA_EDGE_NETEM_ENABLED is unset."""
    monkeypatch.delenv("QA_EDGE_NETEM_ENABLED", raising=False)
    with patch("subprocess.run") as mock_run:
        clear_netem()  # must not raise
    mock_run.assert_called_once()


def test_clear_netem_raises_on_non_linux(monkeypatch):
    """OS guard still applies — clearing on non-Linux means the user
    set up something wrong."""
    monkeypatch.setattr("mk_qa_master.edge.resilience.sys.platform", "darwin")
    with pytest.raises(RuntimeError, match="requires Linux"):
        clear_netem()


# ---- kill_ffmpeg_subprocess -----------------------------------------------

def test_kill_ffmpeg_subprocess_schedules_terminate():
    """Background thread terminates each subprocess in handle._procs
    after the scheduled delay."""
    p1 = MagicMock()
    p2 = MagicMock()
    handle = MagicMock(_procs=[p1, p2])

    kill_ffmpeg_subprocess(handle, after_seconds=0.05)

    # Give the daemon thread enough time to fire.
    time.sleep(0.2)

    p1.terminate.assert_called_once()
    p2.terminate.assert_called_once()


def test_kill_ffmpeg_subprocess_swallows_terminate_errors():
    """If a process is already dead (terminate raises), we don't crash
    the test — other procs in the list still get hit."""
    p1 = MagicMock()
    p1.terminate.side_effect = ProcessLookupError("already exited")
    p2 = MagicMock()
    handle = MagicMock(_procs=[p1, p2])

    kill_ffmpeg_subprocess(handle, after_seconds=0.05)
    time.sleep(0.2)

    # p2 still got hit despite p1's error.
    p2.terminate.assert_called_once()


def test_kill_ffmpeg_subprocess_handles_empty_procs_list():
    """No procs to kill → no-op, no crash. (Pass-through SourceHandle
    case: `rtsp://` source had nothing local to spawn.)"""
    handle = MagicMock(_procs=[])
    kill_ffmpeg_subprocess(handle, after_seconds=0.05)
    time.sleep(0.1)
    # No assertion — just confirms no exception.


# ---- build_corrupted_gop_fixture ------------------------------------------

def test_build_corrupted_gop_fixture_invokes_ffmpeg_with_noise_filter(
    _force_linux,
):
    """ffmpeg invocation includes the -bsf:v noise=N flag that produces
    a clip cv2 will choke on partway through."""
    with patch("subprocess.run") as mock_run:
        build_corrupted_gop_fixture(
            input_path="factory.mp4",
            output_path="corrupted.mp4",
            corrupt_at_second=3,
        )
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "ffmpeg"
    assert "factory.mp4" in cmd
    assert "corrupted.mp4" in cmd
    assert "-bsf:v" in cmd
    # The next arg after -bsf:v is `noise=10000`.
    bsf_idx = cmd.index("-bsf:v")
    assert cmd[bsf_idx + 1].startswith("noise=")
    assert mock_run.call_args[1].get("check") is True


def test_corrupted_gop_fixture_passes_corrupt_timestamp(_force_linux):
    """corrupt_at_second=5 propagates into the select filter expression."""
    with patch("subprocess.run") as mock_run:
        build_corrupted_gop_fixture(
            input_path="factory.mp4",
            output_path="corrupted.mp4",
            corrupt_at_second=5,
        )
    cmd = mock_run.call_args[0][0]
    vf_idx = cmd.index("-vf")
    select_expr = cmd[vf_idx + 1]
    # Filter syntax: select='between(t,5,6)*0+1'
    assert "between(t,5,6)" in select_expr


def test_corrupted_gop_fixture_raises_on_non_linux(monkeypatch):
    """OS guard fires."""
    monkeypatch.setattr("mk_qa_master.edge.resilience.sys.platform", "darwin")
    with pytest.raises(RuntimeError, match="requires Linux"):
        build_corrupted_gop_fixture("in.mp4", "out.mp4")
