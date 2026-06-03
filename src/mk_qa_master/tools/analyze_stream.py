"""v1.1.0 — analyze_stream MCP tool (parallel to analyze_url / analyze_screen).

Probes an RTSP stream's basic properties (resolution, fps) + optionally
reads an annotations sidecar to derive candidate test cases (one per
known label + the four runner-standard checks: throughput, latency
SLA, reconnect, empty-frame).

Vendor-host blacklist (§11 #6): refuses RTSP URLs at known surveillance
/ IoT camera vendor domains by default. Set QA_EDGE_ALLOW_VENDOR_HOSTS=true
to override for own-camera testing.

cv2 is imported lazily so the tool surface stays import-safe on a base
install — `inspect_tools()` enumerating the surface won't crash without
[edge] extras.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from ..config import EdgeConfig
from ..edge.ground_truth import load_annotations


# Default-on blacklist. These are public-facing brands whose products
# are most commonly deployed as accessible surveillance cameras. We
# refuse hostnames ending with any of these substrings unless the
# operator explicitly opts in.
_VENDOR_HOST_BLACKLIST: tuple[str, ...] = (
    "dahua.com",
    "hikvision.com",
    "ezviz.com",
    "axis.com",
    "amcrest.com",
    "lorex.com",
    "swann.com",
    "reolink.com",
)


def analyze_stream(arguments: dict[str, Any]) -> dict[str, Any]:
    """Tool entry point.

    Args:
      rtsp_url: str — required. The stream to probe.
      annotations_path: str — optional. JSON sidecar with per-frame
        expected detections; when supplied, candidate_tcs lists one
        per discovered label.

    Returns:
      {url, width, height, fps, labels, candidate_tcs} on success, or
      {error, hint} on rejection / probe failure.
    """
    arguments = arguments or {}
    rtsp_url = (arguments.get("rtsp_url") or "").strip()
    annotations_path = (arguments.get("annotations_path") or "").strip()

    if not rtsp_url:
        return {
            "error": "bad_request",
            "hint": "rtsp_url is required (rtsp:// URL or path that will "
                    "be served via mediamtx).",
        }

    # §11 #6 vendor-host blacklist — refuses surveillance vendor
    # domains by default.
    cfg = EdgeConfig()
    if not cfg.allow_vendor_hosts:
        host = (urlparse(rtsp_url).hostname or "").lower()
        if host and any(host.endswith(v) for v in _VENDOR_HOST_BLACKLIST):
            return {
                "error": "forbidden_vendor_host",
                "hint": (
                    f"RTSP URL points at a known surveillance / IoT "
                    f"camera vendor domain ({host!r}). This is "
                    "default-blocked to keep accidental probing of "
                    "public camera feeds off the default path. If "
                    "you own this camera and want to test against it, "
                    "set QA_EDGE_ALLOW_VENDOR_HOSTS=true."
                ),
                "blocked_host": host,
            }

    # cv2 import deferred until we actually need to probe — keeps
    # the tool registration import-safe on base install.
    try:
        import cv2  # type: ignore[import-not-found]
    except ImportError as e:
        return {
            "error": "missing_extras",
            "hint": (
                'analyze_stream requires the [edge] extras. Install '
                'with: pip install "mk-qa-master[edge]". '
                f"Underlying ImportError: {e}"
            ),
        }

    cap = cv2.VideoCapture(rtsp_url)
    try:
        if not cap.isOpened():
            return {
                "error": "stream_unreachable",
                "hint": (
                    f"cv2.VideoCapture could not open {rtsp_url!r}. "
                    "Check the URL is reachable + a producer is running "
                    "(file source: ffmpeg streaming to mediamtx; remote: "
                    "the camera is online)."
                ),
                "url": rtsp_url,
            }
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(cap.get(cv2.CAP_PROP_FPS))
    finally:
        cap.release()

    labels: list[str] = []
    if annotations_path:
        try:
            frames, _ = load_annotations(annotations_path)
            seen: set[str] = set()
            for objs in frames.values():
                for o in objs:
                    label = o.get("label")
                    if label:
                        seen.add(str(label))
            labels = sorted(seen)
        except FileNotFoundError:
            # Non-fatal — proceed without label-driven TCs.
            labels = []
        except Exception:
            labels = []

    # Build candidate_tcs. Match analyze_url's contract: strings (PRD
    # §11 #5). Per-label entries first, then the runner-standard
    # invariants every edge suite cares about.
    candidate_tcs: list[str] = [
        f"frames containing {label} should be detected within the IoU threshold"
        for label in labels
    ]
    candidate_tcs.extend([
        "overall throughput should be >= the configured min_fps",
        "single-frame p95 latency should be <= the latency SLA",
        "stream reconnects after mid-test interruption without crashing",
        "empty / no-target frames do not generate false-positive detections",
    ])

    return {
        "url": rtsp_url,
        "width": width,
        "height": height,
        "fps": fps,
        "labels": labels,
        "candidate_tcs": candidate_tcs,
    }
