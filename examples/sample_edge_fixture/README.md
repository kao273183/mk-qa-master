# Edge Runner Sample Fixture

v1.1.1 housekeeping — the binary fixture the v1.1.0 PRD §3 mentioned, deferred to a patch so the v1.1.0 release didn't block on asset creation.

This directory holds the smallest possible end-to-end fixture for the Edge AI Runner: a 75KB synthetic test video + an annotations sidecar. The goal is **plumbing verification**, not realistic Edge AI testing.

## What's here

| File | Size | Source |
|---|---|---|
| `factory.mp4` | ~75KB | ffmpeg synthetic test pattern (`testsrc` filter, 320×240, 5 fps, 10 s loop) |
| `factory.annotations.json` | ~700B | Hand-written sidecar describing what a real factory feed might contain |

## How the fixture exercises the runner

```bash
pip install "mk-qa-master[edge]"

export QA_RUNNER=edge
export QA_RTSP_SOURCE=$(pwd)/examples/sample_edge_fixture/factory.mp4
export QA_MODEL_PATH=yolov8n.pt
mk-qa-master  # invokes the EdgeInferenceRunner
```

The runner:

1. Starts a local `mediamtx` server + `ffmpeg` to loop `factory.mp4` over RTSP
2. Brings up LocalYolo with `yolov8n.pt` (downloaded on first use)
3. Generates a `test_edge_factory.py` via `generate_test()` (the spec §10 template)
4. Runs pytest, which:
   - Reads frames via `cv2.VideoCapture(EDGE_RTSP_URL)`
   - Pushes each frame through the YOLO backend
   - Tracks per-frame latency
   - Asserts throughput >= `EDGE_MIN_FPS` (default 25)
   - Asserts p95 latency <= `EDGE_LATENCY_SLA_MS` (default 40)
   - Asserts the per-label detection appears in the annotations window (skipped here because the synthetic video doesn't contain real persons/forklifts)

## What works against this fixture

- ✅ Runner lifecycle (setup → pytest → teardown)
- ✅ RTSP source startup + readiness probe
- ✅ `analyze_stream` against the fixture's RTSP URL (when local mediamtx is running)
- ✅ Throughput + latency assertions (the YOLO model still runs against synthetic frames; latency is real, throughput is real)
- ⏭️  Per-label detection (skipped — synthetic video has no real persons/forklifts; assertions remain visible but are decorated with `pytest.mark.skipif(not LABEL, ...)`)

## What needs a real video

Use this fixture for plumbing verification. For real Edge AI testing:

1. Replace `factory.mp4` with actual footage of your target scene
2. Update `factory.annotations.json` with real per-frame ground truth
3. Generate tests via `analyze_stream` against your annotations file
4. The runner's same setup → pytest → teardown chain produces real assertions

## Why a synthetic fixture instead of a real one

PRD §11 #3 caps committed fixtures at 5MB. Real factory / warehouse footage with proper labeling typically runs into hundreds of MB. The synthetic clip is reproducible (regeneratable from the ffmpeg command below), versionable, and exercises the entire runner pipeline.

If you want to regenerate locally:

```bash
ffmpeg -y -f lavfi \
  -i "testsrc=size=320x240:rate=5:duration=10" \
  -c:v libx264 -preset ultrafast -pix_fmt yuv420p \
  examples/sample_edge_fixture/factory.mp4
```

---

*v1.1.1 housekeeping per `docs/v1.1-planning.md` post-v1.1.0 §3 deferred items.*
