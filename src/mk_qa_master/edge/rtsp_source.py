"""Local RTSP source management for Edge runner.

When `QA_RTSP_SOURCE` points at a video file (e.g., `fixtures/factory.mp4`),
the runner starts a local mediamtx server + an ffmpeg process that loops
the file over RTSP. When the env var already starts with `rtsp://`, the
runner passes it through unchanged.

`start_rtsp_source(cfg)` returns a `SourceHandle` whose `.url` is what the
generated test should `cv2.VideoCapture(...)`. `SourceHandle.stop()`
terminates the subprocess(es) the handle owns.

Subprocess lifecycle:

  - mediamtx is started in background. We don't probe its admin port;
    once `_streamable(url)` succeeds we trust the readiness check.
  - ffmpeg streams the local file with `-re -stream_loop -1` so the
    video loops indefinitely. Encoded with libx264 + zerolatency
    tune so the consumer sees frames promptly.
  - Both subprocesses get `stdout=DEVNULL, stderr=DEVNULL` so test
    logs stay clean.

No external Python deps (just stdlib subprocess + socket). cv2 / ffmpeg
binaries are runtime requirements; `pip install "mk-qa-master[edge]"`
gets the Python deps but not the system binaries — the runner's
`setup()` documents that requirement.
"""
from __future__ import annotations

import contextlib
import socket
import subprocess
import time
from dataclasses import dataclass, field


@dataclass
class SourceHandle:
    """Where the consumer should connect + which subprocess(es) to
    kill on teardown.

    `_procs` is private (leading underscore) because the runner
    shouldn't poke at it directly — call `.stop()` instead.
    """
    url: str
    _procs: list[subprocess.Popen] = field(default_factory=list)

    def stop(self) -> None:
        """Terminate every subprocess this handle owns. Suppresses
        errors during teardown — best-effort cleanup, not load-bearing.
        ffmpeg/mediamtx may have already exited (e.g. on Ctrl+C);
        we don't want teardown noise to drown the real test output."""
        for p in self._procs:
            with contextlib.suppress(Exception):
                p.terminate()
                p.wait(timeout=5)


def _streamable(host: str, port: int, timeout: float = 0.5) -> bool:
    """Quick TCP probe — can we open a socket to host:port? Used to
    poll mediamtx + ffmpeg readiness before returning the SourceHandle.
    A successful connect doesn't guarantee RTSP is serving frames yet,
    but it's a strong-enough signal for the polling loop."""
    with contextlib.suppress(Exception):
        with socket.create_connection((host, port), timeout):
            return True
    return False


def start_rtsp_source(cfg) -> SourceHandle:
    """Start whatever's needed to make `cfg.rtsp_source` reachable
    over RTSP. Returns a SourceHandle the runner should `.stop()` in
    teardown.

    Three paths:

      1. `cfg.rtsp_source` is already `rtsp://...` → no local server
         needed; pass through. SourceHandle's `_procs` is empty so
         teardown is a no-op.

      2. `cfg.rtsp_source` is a file path → start mediamtx + ffmpeg,
         wait up to 10 s for the local RTSP port to accept connections,
         then return. If readiness never arrives we return the
         SourceHandle anyway (the test will fail clearly at
         `cv2.VideoCapture` rather than here).

      3. `cfg.rtsp_source` is empty → caller error; we don't crash,
         we hand back a SourceHandle with an empty URL so the
         downstream code path can surface its own clearer error.
    """
    src = cfg.rtsp_source
    if not src:
        return SourceHandle(url="", _procs=[])
    if src.startswith("rtsp://"):
        return SourceHandle(url=src, _procs=[])

    local_url = f"rtsp://localhost:{cfg.rtsp_port}/{cfg.rtsp_path}"

    mediamtx = subprocess.Popen(
        [cfg.mediamtx_bin],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    ffmpeg = subprocess.Popen(
        [
            "ffmpeg", "-re", "-stream_loop", "-1", "-i", src,
            "-c:v", "libx264", "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-f", "rtsp", local_url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Poll for readiness — don't let the test start before the stream
    # is consumable. 10 s ceiling matches the spec's recommendation.
    deadline = time.time() + 10
    while time.time() < deadline and not _streamable("localhost", cfg.rtsp_port):
        time.sleep(0.3)

    return SourceHandle(url=local_url, _procs=[ffmpeg, mediamtx])
