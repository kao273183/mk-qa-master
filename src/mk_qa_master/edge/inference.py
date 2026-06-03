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
    """v1.2.0 — remote inference via HTTP POST.

    Encodes one frame as JPEG (cv2.imencode, quality 85), POSTs as
    multipart/form-data to `cfg.inference_url`, parses the JSON
    response into the same InferResult shape LocalYolo produces.
    Runner code is backend-agnostic — Phase 3's swap doesn't touch
    the runner's setup → pytest → teardown path.

    Expected response shape (PRD §11 #2 — strict contract):
        {"detections": [
            {"label": str, "bbox": [x, y, w, h], "score": float},
            ...
        ]}

    Anything else raises RuntimeError with a clear remediation hint.
    Timeouts read from QA_INFERENCE_TIMEOUT_S (default 10 s; separate
    from QA_DEVICE_TIMEOUT_S which is setup-time only).

    Imports of cv2 + requests are deferred to .infer() so the class
    is constructable on a base install (importable without [edge]
    extras); only failed inference calls surface the missing dep.
    """

    def __init__(self, url: str) -> None:
        self.url = url

    @staticmethod
    def _inference_timeout_s() -> float:
        """Per-inference timeout, separate from the setup-time
        device timeout. Re-read per call so monkeypatch.setenv works
        in tests without instance refresh."""
        import os
        return float(os.environ.get("QA_INFERENCE_TIMEOUT_S", "10"))

    def infer(self, frame: Any) -> InferResult:
        try:
            import cv2  # type: ignore[import-not-found]
            import requests  # type: ignore[import-not-found]
        except ImportError as e:  # pragma: no cover — install-time guidance
            raise RuntimeError(
                "RemoteHTTP backend requires the [edge] extras "
                '(opencv-python + requests). Install with: '
                'pip install "mk-qa-master[edge]". '
                f"Underlying ImportError: {e}"
            ) from e

        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            raise RuntimeError(
                "cv2.imencode failed — frame may be empty or wrong dtype"
            )

        timeout_s = self._inference_timeout_s()
        t = time.perf_counter()
        try:
            response = requests.post(
                self.url,
                files={"image": ("frame.jpg", buf.tobytes(), "image/jpeg")},
                timeout=timeout_s,
            )
            response.raise_for_status()
            data = response.json()
        except requests.Timeout:
            raise RuntimeError(
                f"Inference timeout >{timeout_s}s against {self.url!r}. "
                "Raise QA_INFERENCE_TIMEOUT_S or check device load."
            )
        except requests.ConnectionError as e:
            raise RuntimeError(
                f"Could not reach inference endpoint {self.url!r}: {e}"
            )
        except requests.HTTPError as e:
            raise RuntimeError(
                f"Inference endpoint {self.url!r} returned "
                f"HTTP {response.status_code}: {e}"
            )
        except ValueError as e:  # JSONDecodeError subclasses ValueError
            raise RuntimeError(
                f"Malformed JSON from {self.url!r}: {e}. Expected "
                '{"detections": [{"label", "bbox", "score"}, ...]}'
            )

        try:
            raw_dets = data.get("detections", []) if isinstance(data, dict) else []
            dets = [
                Detection(
                    label=str(d["label"]),
                    bbox=tuple(d["bbox"]),
                    score=float(d["score"]),
                )
                for d in raw_dets
            ]
        except (KeyError, TypeError) as e:
            raise RuntimeError(
                f"Malformed detection record from {self.url!r}: {e}. "
                'Each detection requires `label` (str), `bbox` (4-tuple), '
                "and `score` (float)."
            )

        return InferResult(dets, (time.perf_counter() - t) * 1000)


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
