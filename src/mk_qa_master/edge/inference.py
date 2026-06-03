"""Pluggable inference backends for Edge runner.

Three backend slots are designed; v1.1 ships LocalYolo only:

  - `LocalYolo` — desktop ultralytics YOLO, CPU or GPU. v1.1 default.
  - `RemoteHTTP` — POST one frame to an HTTP service, get JSON back.
    Stubbed in v1.1 (importable, raises NotImplementedError when called)
    so the make_backend factory's branches can stay clean. v1.2 (Phase
    3) fills in the actual HTTP plumbing.

`make_backend(cfg)` picks based on `EdgeConfig`:

  - `QA_INFERENCE_ENDPOINT` set → RemoteHTTP at that URL
  - `QA_JETSON_HOST` set → RemoteHTTP at `http://<host>:8000/infer`
  - else → LocalYolo with `QA_MODEL_PATH` (defaults to yolov8n.pt)

Backend imports are gated so `import mk_qa_master.edge.inference`
succeeds on a base install. Heavy deps (ultralytics, opencv-python,
requests) only resolve when a backend instance is constructed.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class Detection:
    """One model output. Generated tests check `label` (per-frame
    expected) and `bbox` (per-frame IoU)."""
    label: str
    bbox: tuple[float, float, float, float]  # (x, y, w, h)
    score: float


@dataclass
class InferResult:
    """What a single backend.infer(frame) call produces.

    `latency_ms` is the wall-clock for one inference call (model time
    only, not capture / preprocess). LatencyTracker p95 assertions read
    from here.
    """
    detections: list[Detection]
    latency_ms: float

    def has(self, label: str) -> bool:
        return any(d.label == label for d in self.detections)


class InferenceBackend(Protocol):
    """Minimal interface every backend must satisfy."""
    def infer(self, frame: Any) -> InferResult: ...


class LocalYolo:
    """Desktop ultralytics YOLO backend. CPU or CUDA depending on what
    the model file was trained on / what torch finds at runtime.

    The `ultralytics` import is deferred to __init__ so users on a
    base install can still `from .inference import LocalYolo` without
    triggering the torch download chain.
    """

    def __init__(self, model_path: str) -> None:
        from ultralytics import YOLO  # heavy: torch + cuda probe
        self.model = YOLO(model_path)

    def infer(self, frame: Any) -> InferResult:
        t = time.perf_counter()
        # `verbose=False` silences ultralytics's per-call console banner.
        r = self.model(frame, verbose=False)[0]
        dets: list[Detection] = []
        for b in r.boxes:
            x1, y1, x2, y2 = b.xyxy[0].tolist()
            dets.append(Detection(
                label=r.names[int(b.cls)],
                bbox=(x1, y1, x2 - x1, y2 - y1),
                score=float(b.conf),
            ))
        return InferResult(dets, (time.perf_counter() - t) * 1000)


class RemoteHTTP:
    """Stub for v1.2 (Phase 3) — remote inference via HTTP POST.

    Importable in v1.1 so make_backend's branches don't have to
    runtime-check whether the class exists. Construction succeeds
    (just stores the URL); the first `.infer(...)` call raises
    NotImplementedError so a caller who set the env var prematurely
    gets a clear signal rather than a silent 0-detection result.
    """

    def __init__(self, url: str) -> None:
        self.url = url

    def infer(self, frame: Any) -> InferResult:
        raise NotImplementedError(
            "RemoteHTTP backend lands in v1.2 (Phase 3 of theme G). "
            "For v1.1, unset QA_INFERENCE_ENDPOINT / QA_JETSON_HOST "
            "and run the desktop LocalYolo backend."
        )


def make_backend(cfg: Any) -> InferenceBackend:
    """Factory: pick a backend based on EdgeConfig.

    Order of precedence (matches spec §4):
      1. `cfg.inference_url` (QA_INFERENCE_ENDPOINT) — direct service URL
      2. `cfg.jetson_host` (QA_JETSON_HOST) — auto-derives `http://host:8000/infer`
      3. desktop default — LocalYolo against `cfg.model_path`

    Returning an InferenceBackend Protocol-typed value (not a concrete
    class) keeps the runner code backend-agnostic — Phase 3 swaps
    LocalYolo for RemoteHTTP without touching the runner.
    """
    if cfg.inference_url:
        return RemoteHTTP(cfg.inference_url)
    if cfg.jetson_host:
        return RemoteHTTP(f"http://{cfg.jetson_host}:8000/infer")
    return LocalYolo(cfg.model_path)
