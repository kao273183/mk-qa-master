"""v1.3.0 — Resilience injection helpers for Edge runner generated tests.

Theme G Phase 4. Closes the Edge AI Phase arc. Generated tests can
opt into degradation scenarios (network jitter, mid-stream disconnect,
corrupted GOPs) to verify the model + pipeline survive the conditions
production hardware actually hits.

Two safety rails make this safe to import on a base install:

  1. **Linux-only guard** (`_linux_only`): netem uses `tc qdisc` which
     only exists on Linux. Calling helpers on macOS / Windows raises
     RuntimeError with a clear message — generated tests use
     pytest.importorskip + the helper's own raise to skip cleanly.

  2. **Consent gate** (`_netem_enabled`): netem affects ALL loopback
     traffic on the host, not just the test's RTSP stream. Quiet
     auto-enabling could disrupt unrelated processes. The
     `QA_EDGE_NETEM_ENABLED=true` opt-in is the explicit signal that
     the caller understands the side effect.

Imports are deferred for the same reason as the other edge modules —
this file should be importable on a base install; only the helpers
that actually invoke external binaries will fail if the binary is
missing.
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Any


def _linux_only(operation: str) -> None:
    """Raise unless we're on Linux. netem / tc qdisc / ffmpeg-bitstream
    are Linux-specific paths in the resilience flow."""
    if sys.platform != "linux":
        raise RuntimeError(
            f"{operation}() requires Linux (tc qdisc / netem). Skip the "
            "test on macOS/Windows via pytest.mark.skipif or rely on "
            "pytest.importorskip with this module."
        )


def _netem_enabled() -> None:
    """Raise unless QA_EDGE_NETEM_ENABLED is truthy. Prevents accidental
    qdisc setup during normal `QA_RUNNER=edge` runs."""
    if os.getenv("QA_EDGE_NETEM_ENABLED", "").lower() not in ("1", "true", "yes"):
        raise RuntimeError(
            "Resilience netem injection requires "
            "QA_EDGE_NETEM_ENABLED=true. This guard prevents accidental "
            "jitter / packet loss on the loopback interface during "
            "normal runs (netem affects ALL loopback traffic, not just "
            "the test's RTSP stream)."
        )


def apply_netem(jitter_ms: int = 80, loss_pct: int = 2) -> None:
    """Add a netem qdisc to the loopback interface.

    Affects the local mediamtx + ffmpeg → cv2 path. Used inside a
    generated resilience-mode test to verify the model + pipeline
    survive realistic network conditions.

    Both guards apply: must be Linux + QA_EDGE_NETEM_ENABLED=true.

    Args:
      jitter_ms: per-packet latency injection (uniform distribution)
      loss_pct: percentage of packets to drop randomly
    """
    _linux_only("apply_netem")
    _netem_enabled()
    subprocess.run(
        ["tc", "qdisc", "add", "dev", "lo", "root", "netem",
         "delay", f"{jitter_ms}ms", "loss", f"{loss_pct}%"],
        check=True,
    )


def clear_netem() -> None:
    """Tear down the qdisc. Safe to call even when nothing's set up.

    Best-effort: `tc qdisc del` returns non-zero when no qdisc exists,
    but we don't want teardown errors to crash the test fixture.
    Hence `check=False`.

    Only requires Linux — no consent gate (clearing is always safe).
    """
    _linux_only("clear_netem")
    subprocess.run(
        ["tc", "qdisc", "del", "dev", "lo", "root"],
        check=False,
    )


def kill_ffmpeg_subprocess(handle: Any, after_seconds: int = 5) -> None:
    """Schedule a mid-stream ffmpeg kill via a background thread.

    Used to verify the test catches stream-reconnect / dropped-frame
    behavior rather than crashing with a Python-level exception. The
    `handle` is a SourceHandle from `mk_qa_master.edge.rtsp_source` —
    we walk its private `_procs` list and `.terminate()` everything.

    Daemon thread so it doesn't block pytest shutdown if the test
    finishes before the timer fires. Errors during terminate are
    swallowed — the producer process may have already exited.

    Cross-platform (uses threading + subprocess.Popen.terminate which
    works everywhere); no `_linux_only` guard.
    """
    import threading
    import time

    def _kill() -> None:
        time.sleep(after_seconds)
        for proc in getattr(handle, "_procs", []) or []:
            try:
                proc.terminate()
            except Exception:
                pass

    threading.Thread(target=_kill, daemon=True).start()


def build_corrupted_gop_fixture(
    input_path: str, output_path: str, corrupt_at_second: int = 3,
) -> None:
    """Produce a clip with a corrupted GOP starting at corrupt_at_second.

    Used by resilience-mode tests to verify the suite catches
    frame-decode failures (cv2 returning ok=False) rather than crashing
    with an unhandled exception.

    Uses ffmpeg's `noise` bitstream filter to inject randomness into
    the encoded H.264 stream — cv2's decoder will choke on the affected
    GOPs. Output is short enough to keep CI fast.

    Linux-only because the resilience suite as a whole is Linux-only.
    The fixture COULD be built on macOS, but we don't ship a build path
    for it to keep the helper consistent with apply_netem's gating.

    Args:
      input_path: clean source clip to corrupt (typically the v1.1.1
        bundled examples/sample_edge_fixture/factory.mp4)
      output_path: where to write the corrupted clip
      corrupt_at_second: timestamp where corruption begins
    """
    _linux_only("build_corrupted_gop_fixture")
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", input_path,
            "-vf", (
                f"select='between(t,{corrupt_at_second},"
                f"{corrupt_at_second + 1})*0+1'"
            ),
            "-c:v", "libx264", "-preset", "ultrafast",
            "-bsf:v", "noise=10000",
            output_path,
        ],
        check=True,
    )
