"""v1.1.0 — Edge AI Inference Runner support modules.

Theme G from docs/v1.1-planning.md §3, Phase 1+2 bundled. This package
holds the runner-agnostic pieces:

  - rtsp_source: manage local mediamtx + ffmpeg when QA_RTSP_SOURCE is
    a file; pass-through when it's already an rtsp:// URL
  - inference: pluggable InferenceBackend Protocol + LocalYolo (desktop)
    + make_backend(cfg) factory. RemoteHTTP variants land in v1.2 (Phase 3)
  - metrics: IoU + match_detection + LatencyTracker (sized for the
    p95 latency SLA assertion the generated tests use)
  - ground_truth: load and normalize annotations sidecars (per-frame
    expected detections)

Heavy optional deps (ultralytics, opencv-python) live behind the
`mk-qa-master[edge]` extras — see pyproject.toml. The modules in this
package gate their imports so `import mk_qa_master.edge.*` succeeds
on a base install; the deps only load when the corresponding code path
runs.
"""
