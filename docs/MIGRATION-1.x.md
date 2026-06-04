# Migration Guide — mk-qa-master 1.x

This document logs additive shape changes within the v1.x line. v0.x → v1.0 changes live in [`MIGRATION-0.x-to-1.0.md`](MIGRATION-0.x-to-1.0.md). Future breaking changes (renames, removals, type changes) require a v2.0 bump per [`DEPRECATION-POLICY.md`](DEPRECATION-POLICY.md) — those will get a separate `MIGRATION-1.x-to-2.0.md` when v2.0 work opens.

Every entry is **additive**. v1.0.0 → v1.1.0 callers that ignore newly-added fields keep working unchanged.

---

## TL;DR for upgraders

If you're on v1.0.0, upgrading to v1.1.0 is a drop-in:
- 21 v1.0-frozen tools all unchanged in shape
- 1 new tool: `analyze_stream` (Edge AI Runner — only consumed when you opt into the `[edge]` extras)
- `get_runner_info` now lists 2 new available runners (`edge`, `rtsp`) — both alias the same `EdgeInferenceRunner` class

If you don't want Edge AI, ignore the new tool. The base install is unchanged.

---

## Change log — v1.0 → v1.1

### v1.0.0 → v1.1.0 (Edge AI Runner, Theme G Phases 1+2)

**New MCP tool: `analyze_stream`** (tool count 21 → 22, snapshot ack consumed).

Probes an RTSP stream's basic geometry (width, height, fps) and optionally reads an annotations sidecar to surface per-label candidate test cases. The shape parallels `analyze_url` / `analyze_screen`:

```jsonc
// Input
{
  "rtsp_url": "rtsp://camera.local:554/feed",  // required
  "annotations_path": "fixtures/factory.annotations.json"  // optional
}

// Success
{
  "url": "rtsp://camera.local:554/feed",
  "width": 1920,
  "height": 1080,
  "fps": 30.0,
  "labels": ["person", "forklift"],
  "candidate_tcs": [
    "frames containing person should be detected within the IoU threshold",
    "frames containing forklift should be detected within the IoU threshold",
    "overall throughput should be >= the configured min_fps",
    "single-frame p95 latency should be <= the latency SLA",
    "stream reconnects after mid-test interruption without crashing",
    "empty / no-target frames do not generate false-positive detections"
  ]
}

// Error envelopes (additive, follow the existing error_kind pattern)
{ "error": "bad_request", "hint": "..." }
{ "error": "forbidden_vendor_host", "hint": "...", "blocked_host": "camera.dahua.com" }
{ "error": "missing_extras", "hint": "..." }
{ "error": "stream_unreachable", "hint": "...", "url": "..." }
```

**Vendor-host blacklist** (default-on). `analyze_stream` refuses RTSP URLs pointing at known surveillance / IoT camera vendor domains (Dahua / Hikvision / Ezviz / Axis / Amcrest / Lorex / Swann / Reolink). The override is `QA_EDGE_ALLOW_VENDOR_HOSTS=true` for own-camera testing.

**New runner: `EdgeInferenceRunner`** registered as `edge` (canonical) and `rtsp` (alias). Set `QA_RUNNER=edge` to drive RTSP-stream + inference-device test scenarios. Sample lifecycle:

```bash
# Desktop mode (LocalYolo)
export QA_RUNNER=edge
export QA_RTSP_SOURCE=fixtures/factory.mp4
export QA_MODEL_PATH=yolov8n.pt
mk-qa-master  # runner brings up mediamtx + ffmpeg, runs pytest with EDGE_* env vars set
```

**Optional extras**: `pip install "mk-qa-master[edge]"`. Installs `opencv-python>=4.9`, `ultralytics>=8,<9`, `requests>=2.31`. Base install (`pip install mk-qa-master`) doesn't pull these heavy deps — the new `analyze_stream` tool surfaces `error: missing_extras` instead of crashing if you call it without the extras.

**New env vars (all `QA_*`-prefixed, all optional)**:

| Env var | Purpose | Default |
|---|---|---|
| `QA_RTSP_SOURCE` | File path or `rtsp://` URL | empty |
| `QA_RTSP_PORT` / `QA_RTSP_PATH` | Local mediamtx server | `8554` / `cam` |
| `QA_JETSON_HOST` | Future Phase 3 remote inference (v1.2) | empty |
| `QA_INFERENCE_ENDPOINT` | Direct remote inference URL (v1.2) | empty |
| `QA_MODEL_PATH` | LocalYolo model file | `yolov8n.pt` |
| `QA_MIN_FPS` / `QA_LATENCY_SLA_MS` / `QA_IOU_THRESHOLD` | Threshold defaults the generated tests assert | `25` / `40` / `0.5` |
| `QA_MEDIAMTX_BIN` | Path to the mediamtx binary | `./mediamtx` |
| `QA_DEVICE_TIMEOUT_S` | Network timeout extension | `60` |
| `QA_EDGE_ALLOW_VENDOR_HOSTS` | Override vendor-host blacklist | `false` |

**Action required**: none. Skip the new tool + runner if you don't need them. Existing tools and runners are unchanged.

---

## What stays stable from v1.0

The 21 v1.0-frozen tools are still frozen (now 22 total with the additive `analyze_stream`). The v1.0 stability promise from [`MIGRATION-0.x-to-1.0.md`](MIGRATION-0.x-to-1.0.md) "What stays stable forever" still applies:

- 21 v1.0 tool names — none renamed, none removed
- All consent gate env vars
- Plan / bookend shapes
- Hard-stop blacklists

v1.1 adds `analyze_stream` as the 22nd frozen tool. From v1.1 forward, removing `analyze_stream` would require a v2.0 bump with a deprecation cycle.

---

## How to deliberately evolve the schema in v1.x

Same mechanism as v1.0:

1. Set `BREAKING_CHANGE_ACK=true` in the PR's CI env
2. Add an entry to **this file** (v1.x successor entries get appended here; once v2.0 opens, a new `MIGRATION-1.x-to-2.0.md` takes over)
3. The snapshot test rewrites itself when both `MIGRATION-*.md` files exist alongside `DEPRECATION-POLICY.md`

PR #83 (the v1.1 `analyze_stream` addition) is the first real use of this mechanism — it worked. The pattern is now battle-tested for future v1.x additions.

---

*Last updated: 2026-06-03 (v1.2.1 — relicense announcement). Cross-reference: [`MIGRATION-0.x-to-1.0.md`](MIGRATION-0.x-to-1.0.md), [`DEPRECATION-POLICY.md`](DEPRECATION-POLICY.md), [`prd-v1.1-edge-ai-runner.md`](prd-v1.1-edge-ai-runner.md), [`prd-v1.2-edge-ai-phase-3.md`](prd-v1.2-edge-ai-phase-3.md), [`RELICENSING.md`](RELICENSING.md).*

---

### v1.2.0 → v1.2.1 (Relicense announcement — MIT → Apache 2.0 planned for v2.0.0)

**No code changes. No surface changes. Pure documentation.**

This patch starts the deprecation clock for a license change. The actual relicense lands in v2.0.0; v1.2.1 only announces the plan so downstream users who have license-review requirements have time to react.

**What changes**:
- New `docs/RELICENSING.md` document covering the rationale, timeline, user impact, and mechanical v2.0 checklist
- New "License Evolution Plan (v1.2.1 announcement)" section in `README.md`
- New "License changes" section in `docs/DEPRECATION-POLICY.md` codifying the rule that license changes are major-version-only with ≥ 1 minor of announcement

**What does NOT change**:
- The `LICENSE` file (still MIT)
- `pyproject.toml`'s `license` field (still MIT)
- Plugin manifests (still MIT)
- Any source-file header
- Any tool / API / response shape

**Action required for v1.x users**: none. v1.x stays MIT for every future release through v1.x.y. Apache 2.0 only applies when (and if) you upgrade to v2.0+.

See `RELICENSING.md` for the full timeline and the v6+ month v1.x bugfix commitment after v2.0 ships.

---

## Change log — v1.1 → v1.2

### v1.1.2 → v1.2.0 (Edge AI Runner Phase 3 — Remote Inference)

**No new MCP tools. No tool count change** (stays at 22). Phase 3 fills in stubs the v1.1 release deliberately left empty.

**`RemoteHTTP.infer()` now actually works.** v1.1 raised `NotImplementedError("v1.2 / Phase 3")`. v1.2.0 ships the real implementation:

- JPEG-encode the frame via `cv2.imencode(".jpg", frame, [IMWRITE_JPEG_QUALITY=85])`
- POST as `multipart/form-data` to `cfg.inference_url` with `{"image": <bytes>}`
- Parse JSON response: `{"detections": [{"label", "bbox", "score"}, ...]}`
- Return `InferResult(detections=[...], latency_ms=...)` — same shape as `LocalYolo`

Setting `QA_JETSON_HOST` or `QA_INFERENCE_ENDPOINT` against v1.2.0 now drives real remote inference. v1.1 users who got the `NotImplementedError` should drop the workaround.

**`EdgeInferenceRunner._healthcheck_device()` now performs a real `GET /health` probe.** v1.1 only checked the URL shape; v1.2.0 actually hits the network. URL derivation:

| Inference URL | Probed `/health` URL |
|---|---|
| `http://jetson:8000/infer` | `http://jetson:8000/health` |
| `http://dev:9000/infer` | `http://dev:9000/health` |
| `http://dev/myapi/predict` (non-conforming) | `http://dev/myapi/predict/health` |

Trailing `/infer` swap is the Jetson convention; other endpoints get `/health` appended.

**New optional env var `QA_INFERENCE_TIMEOUT_S`** — per-inference timeout (default 10s), separate from setup-time `QA_DEVICE_TIMEOUT_S` (default 60s). Tune the two independently:

| Knob | Default | Used by |
|---|---|---|
| `QA_DEVICE_TIMEOUT_S` | 60s | `_healthcheck_device()` GET /health |
| `QA_INFERENCE_TIMEOUT_S` | 10s | `RemoteHTTP.infer()` per-frame POST |

**`init_qa_knowledge` response gains optional `runner_section_included: bool`.** When `QA_RUNNER=edge` (or `rtsp` alias), the field signals that the bundled "Edge Vision Inference Testing" methodology section is the runner-relevant starting point. The section was already in the v1.1.1 methodology — this is a discoverability hint, not a content change. The `next_step` message also gains a one-line pointer at the Edge section (EN + zh-tw both updated).

| Runner | `runner_section_included` |
|---|---|
| Unset / `pytest` / `jest` / etc. | `false` |
| `edge` or `rtsp` | `true` |

Callers that ignore the new field keep working unchanged.

**Side fix**: `init_qa_knowledge` now uses `.replace("{project_name}", ...)` instead of `.format()`. The pre-existing `.format()` call broke on the bundled methodology's JSON examples (RFC 7807 problem-details `{type, title, status, detail, instance}` and similar) — calls with the default body would `KeyError` out. Caught by v1.2.0 PR-4's new test coverage. Behavior identical for users who weren't hitting the bug.

**Action required**: none. v1.1.x → v1.2.0 is additive. New env vars are optional. New response field is optional. The `RemoteHTTP` + `_healthcheck_device` upgrades only matter to users who'd already opted into remote inference via env vars.

**Three carry-forward housekeeping items closed** (v0.11 / v1.0 / v1.1 postmortems):
- `.github/workflows/ci.yml` gained a `stability-lock` job that fails when `BREAKING_CHANGE_ACK=true` is set but no `docs/MIGRATION-*.md` was edited in the PR
- `.github/PULL_REQUEST_TEMPLATE/` gained four templates (`feat-runner`, `feat-tool`, `feat-bookend`, `release`)
- v1.2 PRD §11 #5 response-shape lock expansion: `test_v1_schema_snapshot.py` now also asserts bookend tools' descriptions mention `plan_verification`

---

### v1.2.1 → v1.3.0 (Edge AI Runner Phase 4 — Resilience + Coach)

**No new MCP tools. Tool count stays at 22.** Phase 4 closes the Edge AI Phase arc — Phases 1+2 (v1.1), 3 (v1.2), 4 (this) — and ships an opt-in resilience-injection layer plus 4 Edge-specific flake signals.

**New module `mk_qa_master.edge.resilience`** with four helpers (all Linux-only via `_linux_only()` guard; `apply_netem` additionally gated on `QA_EDGE_NETEM_ENABLED=true` consent):

| Helper | Purpose |
|---|---|
| `apply_netem(jitter_ms=80, loss_pct=2)` | `tc qdisc add` netem to lo — affects local mediamtx/ffmpeg/cv2 path |
| `clear_netem()` | Tears down qdisc; `check=False` so teardown never crashes |
| `kill_ffmpeg_subprocess(handle, after=5s)` | Daemon-thread schedules `.terminate()` on SourceHandle._procs |
| `build_corrupted_gop_fixture(in, out, t=3)` | ffmpeg `-bsf:v noise=10000` to make cv2 choke on affected GOPs |

**`get_optimization_plan` gains 4 Edge-specific flake signals** in the suite-quality lens. They read the new optional `edge_metrics` block on per-test report.json entries (added by the edge runner; absent on non-edge runs):

| Signal | Priority | Triggers when |
|---|---|---|
| `edge_latency_p95_exceeded_sla` | 🔴 high | current run's `p95_latency_ms` > `EDGE_LATENCY_SLA_MS` (env, 40ms default) |
| `edge_fps_variance_across_runs` | 🟡 medium | relative stddev > 0.2 across ≥5 runs of the same nodeid |
| `edge_iou_jitter_per_tc` | 🟡 medium | stddev(iou_per_frame) > 0.1 across ≥5 samples |
| `edge_coverage_gap_per_label` | 🟡 medium | label in `edge_metrics.labels_covered` but no nodeid contains it |

Non-edge suites see no signal changes — `_analyze_edge_signals` returns `{}` when no test carries the block. The `suite_quality` block of `get_test_report`'s response gains an additive `edge_signals` key alongside the existing `tests` / `by_category` / `total_tests`. `get_test_report`'s tool description was updated to mention `edge_metrics` (per PRD §11 #2 description-text response-shape lock).

**`generate_test` gains optional `resilience_mode='netem'`** keyword arg. When set, the generated pytest file gets a session-scoped autouse `_resilience` fixture that wraps detection + throughput tests with `apply_netem` / `clear_netem`. The fixture uses `pytest.importorskip` + try/except on `RuntimeError` so it cleanly skips when the resilience module isn't importable, `QA_EDGE_NETEM_ENABLED` is unset, or `sys.platform != "linux"`. Default (omit / empty string) emits no resilience artifacts — strict backward compat with v1.1/v1.2 generated tests.

**New optional env var `QA_EDGE_NETEM_ENABLED`** (default `false`). Required by `apply_netem` because netem affects ALL loopback traffic — not just the test's RTSP stream — so quiet auto-enabling could disrupt unrelated host processes.

**Theme J sweep**: `skills/mk-qa-master/reference/tool-surface.md` gained `analyze_stream` in its "stable since v1.1" reference list. ~5 minute housekeeping; purely doc.

**Action required**: none for v1.x users on web/mobile/API runners. Edge AI users can opt into resilience-mode tests by passing `resilience_mode='netem'` to `generate_test`; flake signals appear automatically once any test entry carries `edge_metrics`.

**v1.3.0 → v2.0 timing**: per `docs/RELICENSING.md` strict cycle (PRD §11 #6 ratified) — v1.3 ships within 2 weeks of merge; v2.0 (the MIT → Apache 2.0 relicense) follows after **≥ 1 calendar month** of v1.3.0 being on PyPI. The v1.x bugfix line continues for ≥ 6 months after v2.0.0 ships per `docs/DEPRECATION-POLICY.md` §"License changes".

### v1.3.0 → v1.3.1 (Edge AI HTML report parity — patch)

**Pure bugfix patch.** No surface change, no new env vars, no new tools.

**Bug**: `EdgeInferenceRunner` didn't override `get_all_test_details()` (defaults to `[]` on `TestRunner`). The HTML reporter (`mk_qa_master.reporters.html.render_report`) prefers that override and only falls back to `get_failure_details()` when it's empty — but `get_failure_details()` only yields failed tests. The combined effect: on an all-green Edge run (0 failures / 1 passed / 1 skipped), the report rendered only the top stat bar and the green "所有測試通過" placeholder, hiding every per-test row that operators actually need (nodeid, duration, latency, fps, labels).

**Fix**:
- `EdgeInferenceRunner.get_all_test_details()` now reads `report.json` and returns per-test dicts with `{nodeid, outcome, duration, message}` — and forwards the v1.3.0 additive `edge_metrics` block when present.
- HTML reporter gains `_render_edge_metrics_html()` + `.edge-metrics` CSS block. Both pass-cards and fail-cards now surface `p95 latency`, `fps`, `iou (avg / n)`, and `labels` inline when the test entry carries the block. Non-edge runners pay zero HTML cost (helper returns `""` on missing block).
- Label names are HTML-escaped at render time — user-supplied annotation strings can't inject markup.

**Action required**: none. Existing Edge users get fully-populated HTML reports on next `generate_html_report` call; web/mobile/API runners see no change.
