# mk-qa-master v1.1.0 — Edge AI Inference Runner, Phases 1+2 (mini-PRD)

**Status:** Draft v0.1 · **Author:** Jack Kao (kao273183) · **Date filed:** 2026-06-03 · **Successor to:** v1.0.0 (Stability Lock) · **Theme picked:** v1.1 planning §3 — Theme G (Phases 1+2 bundled)

Mini-PRD (12 sections, same shape as `docs/prd-v0.10-universal-bookend.md` and `docs/prd-v1.0-stability-lock.md`). v1.1 planning §3 ratified Phase 1+2 bundling: v1.1.0 ships the desktop YOLO runner + the `analyze_stream` MCP tool together. Phases 3 (remote inference) and 4 (resilience + coach) defer to v1.2.0+.

Build-ready technical spec at `mk-qa-master-edge-ai-enhancement.md` — file paths, code samples, env vars, and acceptance criteria all live there. This PRD is the execution contract; the spec is the implementation reference.

---

## 1. Vision

> **`pip install "mk-qa-master[edge]"==1.1.0` → drive a Maestro-style test loop against an RTSP stream.**

The Maestro runner taught us that "device-aware" testing fits cleanly into mk-qa-master's `analyze → generate → run → coach` loop when the runner abstracts the device. Theme G applies the same lens to edge inference: replace "Android device + adb host" with "RTSP source + inference backend", keep everything else.

After v1.1, a QA engineer can:
1. Set `QA_RUNNER=edge`, `QA_RTSP_SOURCE=fixtures/factory.mp4`, `QA_MODEL_PATH=yolov8n.pt`
2. Ask Claude: "analyze this stream and generate detection tests"
3. Get `tests/test_factory_cam.py` with real assertions, run via `pytest`, see HTML report with annotated frames + latency sparkline

Desktop-mode first (no hardware required). Phases 3+4 add Jetson / remote-inference paths and resilience signals.

---

## 2. Problem Statement

User-side: there's no existing path to drive "video stream + inference + assertions" through mk-qa-master. The current 7 runners cover web (pytest-playwright / Jest / Cypress), backend (Go), mobile UI (Maestro), and API (Schemathesis / Newman). The edge-inference gap forces QA engineers in that space to either:
- Roll their own pytest scaffolding per project (no shared structure, no shared analyzer)
- Skip automated inference testing entirely (regression risk on every model update)
- Use vendor-specific tools like NVIDIA's Triton Performance Analyzer (heavy, not pytest-shaped)

Architecturally: the existing `analyze → generate` chain (`analyze_url` for web, `analyze_screen` for mobile) has no parallel for video. Theme G fills that with `analyze_stream` (Phase 2 of the build-ready spec). The runner side (Phase 1) is mostly pytest reuse — same junit, same HTML report, same history archive, same optimizer-feed.

v1.0's stability lock means Theme G's surface additions are *visible*: a new runner shows up in `get_runner_info`'s `available` list, and `analyze_stream` triggers the snapshot ack mechanism + doc-sync test + tool-count refresh.

---

## 3. MVP Scope (v1.1.0)

**In scope (Phases 1+2 from `mk-qa-master-edge-ai-enhancement.md`):**

- New `edge` runner — alias `rtsp`. Same `TestRunner` subclass pattern as `MaestroRunner`. Setup starts RTSP source (mediamtx + ffmpeg when source is a file; passthrough when source is already `rtsp://`); teardown stops it. Run path delegates to existing pytest invocation infrastructure.
- New module tree `src/mk_qa_master/edge/`:
  - `rtsp_source.py` — `SourceHandle` + `start_rtsp_source(cfg)`
  - `inference.py` — `InferenceBackend` Protocol + `LocalYolo` desktop impl + `make_backend(cfg)` factory
  - `metrics.py` — `iou()` + `match_detection()` + `LatencyTracker`
  - `ground_truth.py` — annotations sidecar loader
- New `EdgeConfig` in `config.py` — 8 env vars (per spec §2 table)
- New MCP tool `analyze_stream(rtsp_url, annotations_path)` — returns `{url, width, height, fps, labels, candidate_tcs}`. Tool count **21 → 22**.
- `generate_test` extended with an `edge` template (per spec §10) — produces working pytest with IoU + throughput assertions, not TODO stubs
- `tests_project/conftest.py` ships `backend`, `stream`, `latency` fixtures for the generated tests
- New optional extras: `pip install "mk-qa-master[edge]"` → installs `opencv-python>=4.9`, `ultralytics>=8`, `requests>=2.31`
- Fixture: `examples/sample_edge_fixture/factory.mp4` + `factory.annotations.json` (small enough for CI, real enough for end-to-end)
- New CI job `edge-sample · Python 3.12 + chromium-free` running the desktop path against the fixture
- New `qa-knowledge` section "Domain: Edge Vision Inference" — bundled into `init_qa_knowledge` when `QA_RUNNER=edge`

**Explicitly out of scope (deferred to v1.2.0+):**

- Phase 3: `RemoteHTTP` backend + `QA_JETSON_HOST` health check + remote-device timeout extension
- Phase 4: degradation injection (netem / 斷流 / 壞幀), optimizer Edge flake signals, multi-run resilience analytics
- Training, model optimization, or quantization — out by spec §0
- Cloudflare / hCaptcha integration on video frames — wrong layer
- iOS / Android edge inference via Core ML / TFLite — separate vertical, not v1.1

**v1.1.0 timeline:** **~5–6 working days** of code (Phases 1+2 from the spec sum to 5 days; +1 day for v1.0 stability-lock paperwork that v0.x didn't need).

---

## 4. Architecture — Re-use the v1.0 surface, add one tool + one runner

### Runner registration

```python
# src/mk_qa_master/runners/__init__.py
from .edge_inference import EdgeInferenceRunner
REGISTRY = {
    ...,  # 7 existing runners unchanged
    "edge": EdgeInferenceRunner,
    "rtsp": EdgeInferenceRunner,  # alias for muscle memory
}
```

`get_runner_info` automatically picks up the new entries — no schema change to that tool.

### `analyze_stream` parity with `analyze_url`

| Field | `analyze_url` (web) | `analyze_stream` (edge) |
|---|---|---|
| Probe target | DOM via Playwright | RTSP stream via OpenCV |
| Discovery output | `modules[{kind, name, selectors, candidate_tcs}]` | `{width, height, fps, labels, candidate_tcs}` |
| Candidate TCs | per-module list | per-label + 4 standard (throughput / latency SLA / reconnect / empty-frame) |
| Returns | `{url, page_title, scanned_at, module_count, modules, ...}` | `{url, width, height, fps, labels, candidate_tcs}` |

Same `analyze → candidate_tcs → generate_test` contract. The MCP surface and the host LLM both see consistent shape.

### Generated test wiring

The `edge` template (per spec §10) reads `EDGE_RTSP_URL`, `EDGE_MIN_FPS`, `EDGE_LATENCY_SLA_MS`, `EDGE_IOU_THRESHOLD` from env. The runner's `_edge_env()` populates these from `EdgeConfig` per spec §7. This keeps the generated test runner-agnostic — same test file runs against any backend without modification (Phase 3 backend swap doesn't touch the test).

---

## 5. New Surface Area (v1.0 stability-lock paperwork)

v1.0's snapshot ack + doc-sync mechanism applies. v1.1's PR-1 must:

1. **Update `tests/snapshots/v1/tool_surface.json`** with the new `analyze_stream` entry (22 tools total). Requires `BREAKING_CHANGE_ACK=true` in the PR's CI env.
2. **Update `docs/MIGRATION-0.x-to-1.0.md`** — actually no, that doc only covers v0.x → v1.0. The new home is `docs/MIGRATION-1.x.md` (created in v1.1 PR-1) recording v1.x additive entries. v0.10 → v1.0 → v1.1 looks like:
   - v0.10 → v1.0: no shape change
   - v1.0 → v1.1: `analyze_stream` added; `get_runner_info`'s available-runners list grows
3. **Update README + SKILL.md + reference/tool-surface.md tool counts**: 21 → 22 across all surfaces. PR-1's doc-sync test catches misses automatically.
4. **Update `_pyproject_version()` floor** in `tests/test_skill_distribution.py` — `(1, 0, 0)` stays; no floor raise needed since v1.1 is minor.
5. **NO `DeprecationWarning` paired test failure** — Theme G is purely additive, no deprecations.

This is the v1.0 stability lock paying off: the paperwork is mechanical, the snapshot test catches drift, the doc-sync test catches stale claims. v0.x's "ship and see if anything broke" becomes v1.x's "declare, snapshot, ship".

---

## 6. Consent / Safety

- **No new consent gate.** Edge inference doesn't touch third-party services by default (desktop YOLO runs locally). Phase 3's `RemoteHTTP` backend may need a consent gate later if it's used to scan production inference services — defer to v1.2.0.
- **Existing v0.7 / v0.8 consent gates unchanged.** `QA_VISUAL_CHALLENGE_CONSENT` and `QA_API_SECURITY_CONSENT` continue to gate their respective tools. Theme G doesn't interact with either.
- **One safety check**: `analyze_stream` refuses RTSP URLs pointing at known surveillance/IoT camera domains (e.g., `*.dahua.com`, `*.hikvision.com` public endpoints). Default-on. The user can override with `QA_EDGE_ALLOW_VENDOR_HOSTS=true` for explicit own-camera testing. This is a small policy decision — pragmatic, not a hard contract.

---

## 7. Tests

### Unit (PR-2 of the v1.1 series — see §8)

```
tests/test_edge_config.py
  test_desktop_mode_when_no_remote_host
  test_jetson_host_flips_to_remote_mode
  test_inference_url_takes_precedence_over_jetson_host

tests/test_edge_metrics.py
  test_iou_perfect_overlap
  test_iou_no_overlap
  test_iou_partial_overlap
  test_match_detection_label_mismatch
  test_match_detection_iou_below_threshold
  test_latency_tracker_p95_with_small_n

tests/test_edge_rtsp_source.py
  test_rtsp_passthrough_when_source_is_already_rtsp_url
  test_local_source_starts_mediamtx_and_ffmpeg  # subprocess.Popen mocked

tests/test_edge_inference.py
  test_make_backend_picks_local_yolo_in_desktop_mode
  test_make_backend_uses_inference_url_when_set
  test_make_backend_falls_back_to_jetson_host  # constructed not started

tests/test_analyze_stream.py
  test_analyze_stream_returns_basic_metadata_without_annotations
  test_analyze_stream_emits_candidate_tcs_per_label
  test_analyze_stream_resolution_below_threshold_flagged_in_candidates
```

### v1.0 contract enforcement (auto)

- `tests/test_v1_schema_snapshot.py` — flagged for ack on PR-1 (analyze_stream addition)
- `tests/test_v1_doc_sync.py` — catches stale tool counts in docs
- `tests/test_v1_deprecation_policy.py` — no-op for v1.1 (no deprecations added)

### CI

New job `edge-sample · Python 3.12` running `examples/sample_edge_fixture/`. Cost: ~5 minutes per run (ffmpeg + mediamtx + small YOLO model + 1 brief pytest run against fixture). Acceptable trade for first-line regression coverage.

---

## 8. Implementation Plan

| PR | Days | Scope |
|---|---|---|
| PR-1 | 1.5 | `EdgeConfig` + `edge/rtsp_source.py` + `edge/inference.py` (LocalYolo only) + `edge/metrics.py` + `edge/ground_truth.py`. Optional extras config. Unit tests for non-runner pieces. |
| PR-2 | 1 | `EdgeInferenceRunner` + REGISTRY wiring + runner unit tests + manual end-to-end against `examples/sample_edge_fixture/`. |
| PR-3 | 1.5 | `analyze_stream` MCP tool + server.py registration + `generate_test` edge template + `tests_project/conftest.py` fixtures. **Snapshot ack triggered here** (analyze_stream is new tool). Doc tool-count refresh (21 → 22). |
| PR-4 | 1 | CI job `edge-sample` + sample fixture commit + README v1.1 section + SKILL.md mention + `init_qa_knowledge` edge domain + `MIGRATION-1.x.md` v1.0 → v1.1 entry. Version bump to 1.1.0 + tag + release. |
| **Total** | **~5d** | |

Each PR is independently merge-able. The snapshot ack lands in PR-3 (the only PR with a tool surface change); PRs 1, 2, 4 don't touch the MCP surface.

---

## 9. Roadmap Context

This PRD lives at `docs/prd-v1.1-edge-ai-runner.md`. After v1.1.0 ships:

- `docs/v1.1-planning.md` §8 gets a postmortem section.
- v1.2 planning opens with Phase 3 (remote inference) as primary recommendation, Phase 4 (resilience + coach) as secondary. Themes C (YAML config) and E (rate-limit) reconsidered as v1.3+ candidates.

v1.1 explicitly **does not** include:
- Phases 3+4 of Theme G — deferred per §3 / v1.1 planning §3
- Theme C (YAML config UX) — v1.2+
- Theme E (OWASP API4 rate limit) — v1.2+
- Cloudflare Turnstile (Theme D) — remains pruned

---

## 10. Decisions Required Before Coding

1. **Optional extras name** — `pip install "mk-qa-master[edge]"` (proposal) vs `[edge-ai]` vs `[edge-inference]`? Recommend: **`[edge]`** — matches `QA_RUNNER=edge`, short, no naming overlap with existing OS-edge concepts.
2. **YOLO version pin** — `ultralytics>=8` (proposal — wide; spec defaults to yolov8n.pt) vs `ultralytics>=8,<9` (tighter — protects against future major bumps that change YOLO output shape)? Recommend: **`>=8,<9`** — v1.0 stability promise favors tight optional-dep ranges.
3. **Sample fixture size** — `factory.mp4` (proposal: ≤ 5MB, < 30 sec) vs ≤ 1MB (faster CI; risk of being too synthetic) vs hosted-elsewhere link? Recommend: **≤ 5MB committed**, target file size enforced via CI check that fails on grow-beyond-budget.
4. **`generate_test` edge template confidence** — does it produce a working pytest on first generation, or does the host LLM need to fill blanks? Recommend: **fully working** (per spec §10). Aligns with the v0.5 "no TODO stubs" invariant.
5. **`analyze_stream` schema for `candidate_tcs`** — strings (proposal, matches existing `analyze_url`) vs dicts with `{description, iou_threshold?, fps_target?}`? Recommend: **strings** — keeps schema lock parity with `analyze_url`. v1.x can add a structured variant additively later.
6. **Vendor-host blacklist for `analyze_stream`** — ship the default-on policy mentioned in §6, or hold the policy for v1.2? Recommend: **ship in v1.1** — it's a small constant + an env override; better to land it before someone reports they pointed it at a public surveillance feed.

---

## 11. Decisions Ratified

Locked 2026-06-03:

1. **Optional extras name `[edge]`** — matches `QA_RUNNER=edge`, short, no naming overlap with OS-edge concepts.
2. **YOLO pinned `ultralytics>=8,<9`** — protects v1.0 stability promise against future major-version output-shape changes.
3. **Sample fixture ≤ 5MB committed** — `factory.mp4` + `factory.annotations.json`. CI size check fails on grow-beyond-budget.
4. **`generate_test` edge template produces fully working pytest** — honors the v0.5 "no TODO stubs" invariant. Generated tests run on first invocation against the sample fixture.
5. **`analyze_stream` `candidate_tcs` are strings** — schema parity with `analyze_url`. A structured variant can be added additively in v1.x without breaking callers.
6. **Vendor-host blacklist ships in v1.1** — small constant set (`*.dahua.com`, `*.hikvision.com` etc.) + `QA_EDGE_ALLOW_VENDOR_HOSTS=true` env override. Lands before someone reports it.

---

## 12. Process Invariants Honored

From `docs/v1.1-planning.md` §5 (which extends v0.10 + v0.11 invariants cumulatively):

1. ✅ Mini-PRD before action — this document, before PR-1.
2. ✅ Dogfood against real artifacts — PR-2 runs the sample fixture end-to-end manually before CI is in place; CI job in PR-4.
3. ✅ Version-sync invariant — PR-4 bumps pyproject + manifests; soft semver test enforces ≥ floor.
4. ✅ PyPI Summary 512-char limit — PR-4's description tweak will pass through the v0.9.5 regression test.
5. ✅ Spike validates produced VALUE — PR-2's manual end-to-end dogfood checks that the generated test actually exercises YOLO + reports throughput, not just that the command runs.
6. ✅ Consent gates document themselves — no new consent gates introduced.
7. ✅ Tool-count refs sync test — v1.0 PR-2 mechanism catches misses automatically.
8. ✅ Soft version-pin tests — v1.0 PR-2 mechanism unchanged.
9. ⏸️ PR description templates — still deferred (v0.11 postmortem §9 #2 noted this carries forward; v1.1 housekeeping or first G PR is the natural slot, but not blocking).
10. ⏸️ CI workflow check for ack + migration-doc pairing — v0.11 postmortem §9 #1 noted this. Reasonable to add in PR-4 of v1.1 since PR-3 is the first ack-triggering minor under the v1.0 contract.
11. ⏸️ Optional-extras CI dep check — new: confirm `mk-qa-master[edge]` resolves cleanly under `pip install` in the new `edge-sample` CI job. Implicit in §7 CI but worth calling out.

---

*End of mini-PRD v0.1 for mk-qa-master v1.1.0. Cross-references: `docs/v1.1-planning.md` (strategic context, B-then-G ratification), `mk-qa-master-edge-ai-enhancement.md` (build-ready technical spec), `docs/prd-v1.0-stability-lock.md` (v1.0 surface contract this builds on additively), `docs/MIGRATION-0.x-to-1.0.md` (additive change log; v1.x successor created in PR-4).*
