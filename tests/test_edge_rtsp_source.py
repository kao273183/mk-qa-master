"""v1.1.0 PR-1 — rtsp_source.start_rtsp_source() + SourceHandle.

Mocks subprocess.Popen + socket.create_connection so the test never
actually starts ffmpeg or mediamtx — we verify the contract (what
subprocess args, what URL returned, teardown cleanup).
"""
from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

from mk_qa_master.edge.rtsp_source import SourceHandle, start_rtsp_source


@dataclass
class _StubCfg:
    """Minimal EdgeConfig shape for these tests."""
    rtsp_source: str = ""
    rtsp_port: int = 8554
    rtsp_path: str = "cam"
    mediamtx_bin: str = "./mediamtx"


# ---- Pass-through path --------------------------------------------------

def test_rtsp_passthrough_when_source_is_already_rtsp_url():
    """When QA_RTSP_SOURCE starts with `rtsp://`, we don't start a
    local server — just return the URL unchanged with an empty _procs
    list (so teardown is a no-op)."""
    cfg = _StubCfg(rtsp_source="rtsp://camera.lan:554/stream")

    with patch("subprocess.Popen") as mock_popen:
        handle = start_rtsp_source(cfg)

    assert handle.url == "rtsp://camera.lan:554/stream"
    assert handle._procs == []
    # No subprocess spawned at all.
    mock_popen.assert_not_called()


def test_empty_source_returns_empty_handle():
    """When QA_RTSP_SOURCE is empty (caller error / unconfigured), we
    don't crash — return an empty SourceHandle so downstream code
    surfaces a clearer error."""
    cfg = _StubCfg(rtsp_source="")
    handle = start_rtsp_source(cfg)
    assert handle.url == ""
    assert handle._procs == []


# ---- Local source path (file → mediamtx + ffmpeg) -----------------------

def test_local_source_starts_mediamtx_and_ffmpeg_subprocesses():
    """File source → start mediamtx + ffmpeg via subprocess.Popen.
    Mock both Popen and the readiness probe so the test runs deterministically
    without waiting for real network/process startup."""
    cfg = _StubCfg(rtsp_source="fixtures/factory.mp4")

    # Each Popen call returns a distinct mock so we can verify the
    # args independently.
    mediamtx_proc = MagicMock(spec_set=["terminate", "wait"])
    ffmpeg_proc = MagicMock(spec_set=["terminate", "wait"])

    popen_calls = []

    def fake_popen(*args, **kwargs):
        popen_calls.append((args, kwargs))
        return mediamtx_proc if len(popen_calls) == 1 else ffmpeg_proc

    # Pretend the local RTSP port is immediately reachable so the
    # readiness loop returns on the first iteration.
    with patch("subprocess.Popen", side_effect=fake_popen), \
         patch("mk_qa_master.edge.rtsp_source._streamable",
               return_value=True):
        handle = start_rtsp_source(cfg)

    # URL points at the local RTSP server.
    assert handle.url == "rtsp://localhost:8554/cam"
    # Two subprocesses tracked, in [ffmpeg, mediamtx] order per source.
    assert handle._procs == [ffmpeg_proc, mediamtx_proc]
    # mediamtx first (Popen call 1), then ffmpeg (call 2).
    mediamtx_args = popen_calls[0][0][0]
    assert mediamtx_args == ["./mediamtx"]
    ffmpeg_args = popen_calls[1][0][0]
    assert ffmpeg_args[0] == "ffmpeg"
    assert "fixtures/factory.mp4" in ffmpeg_args
    assert ffmpeg_args[-1] == "rtsp://localhost:8554/cam"


def test_local_source_readiness_polling_eventually_returns():
    """The readiness loop has a 10 s ceiling. We mock _streamable to
    return False the first 2 calls then True — verifies the loop
    actually exits when the probe succeeds, doesn't run forever."""
    cfg = _StubCfg(rtsp_source="fixtures/loop-readiness.mp4")
    call_count = {"n": 0}

    def streamable_after_three(_host, _port, **_kw):
        call_count["n"] += 1
        return call_count["n"] >= 3

    with patch("subprocess.Popen", return_value=MagicMock()), \
         patch("mk_qa_master.edge.rtsp_source._streamable",
               side_effect=streamable_after_three), \
         patch("time.sleep"):  # don't actually sleep in tests
        handle = start_rtsp_source(cfg)

    # Got the URL — loop exited via the True return rather than the
    # 10 s deadline.
    assert handle.url == "rtsp://localhost:8554/cam"
    assert call_count["n"] >= 3


# ---- SourceHandle teardown ----------------------------------------------

def test_source_handle_stop_terminates_every_subprocess():
    """SourceHandle.stop() walks _procs and calls .terminate() +
    .wait() on each. Exceptions during teardown are suppressed
    (best-effort)."""
    p1 = MagicMock()
    p2 = MagicMock()
    handle = SourceHandle(url="rtsp://test", _procs=[p1, p2])

    handle.stop()

    p1.terminate.assert_called_once()
    p1.wait.assert_called_once_with(timeout=5)
    p2.terminate.assert_called_once()
    p2.wait.assert_called_once_with(timeout=5)


def test_source_handle_stop_swallows_termination_errors():
    """If a process is already dead (Popen.terminate raises ProcessLookupError),
    we don't bubble it up. Other procs in the list still get terminated."""
    p1 = MagicMock()
    p1.terminate.side_effect = ProcessLookupError("already exited")
    p2 = MagicMock()
    handle = SourceHandle(url="rtsp://test", _procs=[p1, p2])

    handle.stop()  # must not raise

    p2.terminate.assert_called_once()


def test_source_handle_stop_with_empty_procs_is_noop():
    """Pass-through path has empty _procs — stop() is a no-op."""
    SourceHandle(url="rtsp://x", _procs=[]).stop()  # must not raise
