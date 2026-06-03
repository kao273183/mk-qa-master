# mk-qa-master v1.2.0 — Edge AI Runner Phase 3 (Remote Inference)

**Status:** Draft v0.1 · **Author:** Jack Kao · **Date filed:** 2026-06-03 · **Successor to:** v1.1.2 (Edge AI Runner Phases 1+2 + README polish) · **Theme picked:** v1.2 planning §3 (Phase 3 alone as v1.2.0; Phase 4 → v1.3)

Mini-PRD (12 sections, same shape as `docs/prd-v0.10-universal-bookend.md`, `docs/prd-v1.0-stability-lock.md`, `docs/prd-v1.1-edge-ai-runner.md`). v1.2 planning §3 ratified Phase 3 alone (no Phase 4 bundle) + 3 carry-forward housekeeping items promoted to required. Build-ready technical reference at `mk-qa-master-edge-ai-enhancement.md` (Phase 3 sections still apply verbatim — the v1.1 implementation just stubbed them out).

---

## 1. Vision

> **`QA_RUNNER=edge` + `QA_JETSON_HOST=192.168.1.50` → real inference on the board.**

v1.1 shipped the desktop YOLO path. Users who set `QA_JETSON_HOST` or `QA_INFERENCE_ENDPOINT` against v1.1.x see a clear `NotImplementedError("v1.2 / Phase 3")` — no silent failure, but no remote inference either. v1.2.0 makes that env-var combination actually do what it says.

After v1.2.0, a QA engineer can:
1. Set `QA_RUNNER=edge`, `QA_JETSON_HOST=192.168.1.50`, `QA_MIN_FPS=15` (Jetson Nano-realistic)
2. Run the same `analyze_stream → generate_test → run_tests` chain v1.1 introduced
3. Inference happens on the board, latency assertions reflect real hardware, results land in the same `report.json`

Zero changes to the AI client's user experience. The runner-level swap is invisible above the env-var layer.

---

## 2. Problem Statement

User-side: v1.1's stub leaves the most common Edge AI scenario (test against your actual edge hardware) unimplemented. Workarounds are bad:
- Maintain a separate fork/branch with the patch applied → fragmentation
- Run desktop YOLO + diff against board YOLO out-of-band → defeats the unified report.json story
- Skip remote inference entirely → can't validate that the same model + same threshold actually passes on target hardware

Architecturally: the v1.1 surface left `RemoteHTTP` constructable but non-functional. Phase 3 is the smallest possible additive change that makes the existing surface honest:
- `RemoteHTTP.infer(frame)` does what `LocalYolo.infer(frame)` does — same return shape, same latency tracking
- `_healthcheck_device()` does what it says (HTTP GET to `/health`, not just a URL-shape check)
- Optional `QA_INFERENCE_TIMEOUT_S` lets users tune per-inference timeout separately from the setup-time `QA_DEVICE_TIMEOUT_S`

Zero MCP surface change. Zero new tools. Zero new runners. v1.0 stability lock honored.

---

## 3. MVP Scope (v1.2.0)

**In scope:**

- `RemoteHTTP.infer(frame)` real implementation:
  - Encode frame via `cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])`
  - POST as `multipart/form-data` to `cfg.inference_url` with `{"image": <bytes>}`
  - Parse JSON response: `{"detections": [{"label", "bbox", "score"}, ...]}`
  - Build `InferResult(detections=[...], latency_ms=elapsed * 1000)`
  - On HTTP error / timeout / malformed JSON: raise a typed exception that the runner can surface as an `error` envelope
- `EdgeInferenceRunner._healthcheck_device()` real probe:
  - `requests.get(<target>/health, timeout=cfg.device_timeout_s)`
  - Accepts 2xx as healthy; anything else raises `RuntimeError` with a clear message
  - Existing URL-shape check kept as the first gate
- New optional env var `QA_INFERENCE_TIMEOUT_S` (default 10s) — read by `RemoteHTTP.infer()`, separate from the setup-time `QA_DEVICE_TIMEOUT_S` (default 60s)
- Doc updates: README troubleshooting block gains 4 new entries (timeout / connection refused / 5xx response / malformed JSON); MIGRATION-1.x adds a v1.1.x → v1.2.0 entry
- 3 housekeeping items promoted per v1.2 planning §4:
  - **CI workflow ack-check** (v1.2 §5 invariant 9 — REQUIRED for v1.2)
  - **PR description templates** (v1.2 §5 invariant 10)
  - **`init_qa_knowledge` runner-aware Edge section selection** (v1.2 §5 invariant 11 — minor additive: response shape gains `runner_section_included: bool` when applicable)

**Explicitly out of scope (deferred to v1.3+):**

- Phase 4: degradation injection (netem / kill-ffmpeg / corrupted-GOP) + Edge flake signals in `get_optimization_plan`
- Theme C: YAML config (bundle with Phase 4 in v1.3)
- Theme E: OWASP API4 rate limit (carry to v1.4)
- Authentication on remote inference endpoints (Phase 3 trusts the network; if you need bearer tokens, defer to v1.3 with a `QA_INFERENCE_AUTH_HEADER` env var)
- Multi-target load balancing (one inference URL only)

**v1.2.0 timeline:** **~3 working days** of code + 0.5d for the 3 housekeeping items + 0.5d release work. ≈ 4 PRs.

---

## 4. Architecture Differences from v1.1

### `RemoteHTTP.infer()` contract

```python
class RemoteHTTP:
    def __init__(self, url: str) -> None:
        self.url = url
        self.timeout_s = float(os.getenv("QA_INFERENCE_TIMEOUT_S", "10"))

    def infer(self, frame: Any) -> InferResult:
        import cv2, requests
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            raise RuntimeError("cv2.imencode failed — frame may be empty")
        t = time.perf_counter()
        try:
            r = requests.post(
                self.url,
                files={"image": ("frame.jpg", buf.tobytes(), "image/jpeg")},
                timeout=self.timeout_s,
            )
            r.raise_for_status()
            data = r.json()
        except requests.Timeout:
            raise RuntimeError(
                f"Inference timeout >{self.timeout_s}s — set QA_INFERENCE_TIMEOUT_S "
                "to a higher value or check device load"
            )
        except requests.ConnectionError as e:
            raise RuntimeError(f"Could not reach {self.url}: {e}")
        except requests.HTTPError as e:
            raise RuntimeError(f"Inference endpoint returned {r.status_code}: {e}")
        except (ValueError, KeyError) as e:
            raise RuntimeError(
                f"Malformed JSON from inference endpoint: {e}. Expected "
                '{"detections": [{"label", "bbox", "score"}, ...]}'
            )

        dets = [
            Detection(label=d["label"], bbox=tuple(d["bbox"]), score=float(d["score"]))
            for d in data.get("detections", [])
        ]
        return InferResult(dets, (time.perf_counter() - t) * 1000)
```

Same `InferResult` shape as `LocalYolo` — the runner code path is backend-agnostic.

### `_healthcheck_device()` contract

```python
def _healthcheck_device(self) -> None:
    target = (
        self.cfg.inference_url
        or (f"http://{self.cfg.jetson_host}:8000/infer"
            if self.cfg.jetson_host else "")
    )
    if not target:
        return

    # v1.1 URL-shape gate kept.
    if not target.startswith(("http://", "https://")):
        raise RuntimeError(
            f"Edge runner: inference target {target!r} doesn't look like "
            "an HTTP URL. Check QA_INFERENCE_ENDPOINT / QA_JETSON_HOST."
        )

    # v1.2.0 — real probe. Derive /health from the inference endpoint.
    health_url = target.rsplit("/", 1)[0] + "/health" if "/infer" in target else target
    try:
        import requests
        r = requests.get(health_url, timeout=self.cfg.device_timeout_s)
        r.raise_for_status()
    except Exception as e:
        raise RuntimeError(
            f"Edge runner: inference target unreachable at {health_url!r} "
            f"({type(e).__name__}: {e}). Set QA_DEVICE_TIMEOUT_S to a larger "
            "value or verify the board is online."
        )
```

### `init_qa_knowledge` runner-aware section selection

```python
def init_qa_knowledge_tool(arguments: dict) -> dict:
    # v1.2.0 — when QA_RUNNER=edge, prepend the Edge Vision Inference
    # section to the scaffolded qa-knowledge.md body.
    runner = os.getenv("QA_RUNNER", "pytest").lower()
    runner_section_included = False
    body = _starter_template()
    if runner in ("edge", "rtsp"):
        edge_section = _builtin_for_lang(QA_LANG).split(
            "## Edge Vision Inference Testing" if QA_LANG == "en"
            else "## 邊緣視覺推論測試"
        )[1]
        body = (
            "## Edge Vision Inference Testing (auto-included for QA_RUNNER=edge)\n"
            f"{edge_section}\n"
            f"{body}"
        )
        runner_section_included = True

    QA_KNOWLEDGE_PATH.write_text(body, encoding="utf-8")
    return {
        "path": str(QA_KNOWLEDGE_PATH),
        "overwrote_existing": ...,
        "runner_section_included": runner_section_included,  # NEW
        "lang": QA_LANG,
    }
```

**Snapshot ack triggered** — `init_qa_knowledge`'s response gains a new optional field. Bundled with the Phase 3 release so the ack cycle is amortized.

---

## 5. Surface Area Implications (v1.0 stability lock paperwork)

v1.2.0 triggers the `BREAKING_CHANGE_ACK=true` mechanism for the second time (v1.1.0 was the first). Required paperwork:

1. **Update `tests/snapshots/v1/tool_surface.json`** — `init_qa_knowledge`'s `inputSchema` is unchanged, but its *response* shape gains an optional field. The snapshot test currently locks `inputSchema` only, so this may NOT trigger a snapshot diff. **Decision point**: do we extend the snapshot to also lock the documented-response shape? Recommend: **yes for v1.2 PR-1** — it's a small change to the snapshot test, but it tightens the contract.
2. **Update `docs/MIGRATION-1.x.md`** with a v1.1.2 → v1.2.0 entry covering:
   - `RemoteHTTP.infer()` now works (was `NotImplementedError`)
   - `_healthcheck_device()` now performs a real probe (was URL-shape only)
   - New optional env var `QA_INFERENCE_TIMEOUT_S`
   - `init_qa_knowledge` response gains optional `runner_section_included` field
3. **Update README tool-count refs** — stays at 22 (no new tool); update env-var table to include `QA_INFERENCE_TIMEOUT_S`
4. **Update SKILL.md** — Edge AI section mentions remote inference is now functional
5. **Bump pyproject** + 2 plugin manifests `1.1.2 → 1.2.0`
6. **CI ack-check workflow step** (v1.2 planning §4 #1 promotion) — lands in PR-1 alongside the other infrastructure

---

## 6. Consent / Safety

- **No new consent gate.** Remote inference talks to a user-configured endpoint (Jetson host or arbitrary URL). The user supplies the URL; we don't auto-discover.
- **Existing vendor-host blacklist** continues to gate `analyze_stream`. Doesn't apply to inference endpoints (those aren't RTSP streams).
- **`QA_INFERENCE_TIMEOUT_S` default 10s** — chosen to fail fast on a stuck endpoint. The 60s `QA_DEVICE_TIMEOUT_S` covers setup-time health probes which can be slow on first board boot.
- **Telemetry hygiene**: errors from `RemoteHTTP.infer()` include the URL in the exception message (for debugging) but **NOT in any persisted telemetry** — same rule as v0.7 token hygiene. The telemetry layer logs boolean outcome only.

---

## 7. Tests

### PR-1 (CI ack-check + foundation)

```
.github/workflows/ci.yml — new step:
  - When BREAKING_CHANGE_ACK=true is set in CI env
  - AND `git diff origin/main...HEAD -- 'docs/MIGRATION-*.md'` is empty
  - → fail with a clear message

tests/test_ci_ack_check.py — meta-test that the workflow step exists
  and triggers on the right condition. Mocks git diff output.
```

### PR-2 (`RemoteHTTP.infer()`)

```
tests/test_edge_inference.py — extend existing file:
  test_remote_http_infer_success_path_mocked
  test_remote_http_infer_timeout_raises_clear_error
  test_remote_http_infer_connection_error_includes_url
  test_remote_http_infer_5xx_response_surfaces_status_code
  test_remote_http_infer_malformed_json_includes_expected_shape
  test_remote_http_inference_timeout_env_var_respected
```

Uses `responses` or `requests_mock` library to mock the HTTP layer. Real HTTP probing happens in the optional dogfood step against a stub Flask service.

### PR-3 (`_healthcheck_device()` + `QA_INFERENCE_TIMEOUT_S`)

```
tests/test_edge_runner.py — extend existing file:
  test_healthcheck_probes_health_endpoint_when_jetson_host_set
  test_healthcheck_probes_inference_url_when_explicitly_set
  test_healthcheck_raises_on_5xx
  test_healthcheck_raises_on_timeout_with_clear_message
  test_healthcheck_respects_device_timeout_s_env_var

tests/test_edge_config.py — extend:
  test_inference_timeout_s_default_is_10
  test_inference_timeout_s_env_override
```

### PR-4 (init_qa_knowledge runner-aware)

```
tests/test_init_qa_knowledge.py — extend (or new if missing):
  test_init_includes_edge_section_when_qa_runner_is_edge
  test_init_does_not_include_edge_section_for_default_runner
  test_init_response_has_runner_section_included_field
  test_init_works_in_both_en_and_zh_tw
```

### v1.0 contract enforcement (auto)

- `tests/test_v1_schema_snapshot.py` — flagged for ack on PR-4 (init_qa_knowledge response shape change)
- `tests/test_v1_doc_sync.py` — catches stale env-var table entries / tool descriptions
- `tests/test_v1_deprecation_policy.py` — no-op (no deprecations)

### CI

The `edge-sample` job from v1.1.1 unchanged. PR-1 adds the new `ack-check` step inline to `ci.yml` (not a separate job; runs on every PR).

---

## 8. Implementation Plan

| PR | Days | Scope | Triggers ack? |
|---|---|---|---|
| PR-1 | 0.5 | CI workflow ack-check step + meta-test + `.github/PULL_REQUEST_TEMPLATE/*.md` files (4 templates: feat-bookend, feat-runner, feat-tool, release) | No (infra only) |
| PR-2 | 1 | `RemoteHTTP.infer()` real implementation + 6 unit tests | No (runner-internal) |
| PR-3 | 0.5 | `_healthcheck_device()` real probe + `QA_INFERENCE_TIMEOUT_S` env var + 7 unit tests + EdgeConfig update | No (runner-internal + new env var) |
| PR-4 | 1 | `init_qa_knowledge` runner-aware section + 4 tests + snapshot ack + MIGRATION-1.x entry + README/SKILL/manifest sync + version bump 1.1.2 → 1.2.0 + tag + release | **YES** (response shape change) |
| **Total** | **~3 days** | | |

PR-1 lands the long-postponed ack-check first so the v1.2 cycle proves the mechanism works on the very next ack-triggering release (PR-4). Three postmortems can finally stop carrying this item forward.

---

## 9. Roadmap Context

This PRD lives at `docs/prd-v1.2-edge-ai-phase-3.md`. After v1.2.0 ships:

- `docs/v1.2-planning.md` §8 gets a postmortem section
- v1.3 planning opens with Phase 4 (resilience) as primary recommendation, Theme C (YAML config) as secondary bundle candidate, Theme E (OWASP API4) as tertiary

v1.2 explicitly **does not** include:
- Phase 4 (resilience + coach) — v1.3
- Theme C (YAML config UX) — v1.3 candidate
- Theme E (OWASP API4 rate limit) — v1.4 candidate
- Cloudflare Turnstile — pruned

---

## 10. Decisions Required Before Coding

1. **`RemoteHTTP.infer()` HTTP framing** — `multipart/form-data` with `{"image": bytes}` (proposal, matches the build-ready spec §4) vs raw bytes POST vs base64-JSON? Recommend: **multipart** — universal HTTP framework support, no base64 overhead, clearest curl-equivalent for debugging.
2. **Expected JSON response shape from the inference endpoint** — `{"detections": [{"label", "bbox", "score"}, ...]}` (proposal, matches `LocalYolo` output) vs accept a wider variety with adapter logic? Recommend: **strict** — document the contract, return clear `malformed_json` error otherwise. v1.x can add an adapter mode additively later.
3. **`/health` URL derivation** — replace trailing `/infer` with `/health` (proposal) vs require a separate `QA_HEALTH_ENDPOINT` env var? Recommend: **derive** — keeps env-var count low. Users with non-conforming endpoints can set `QA_INFERENCE_ENDPOINT=http://host/myapi/predict` and we'll probe `http://host/myapi/health` (close enough; document the convention).
4. **`QA_INFERENCE_TIMEOUT_S` default value** — 10s (proposal) vs 30s (more forgiving)? Recommend: **10s** — fast feedback during dev; tune up for production batch jobs.
5. **Snapshot ack scope expansion** — should the v1.0 snapshot test also lock the documented-response shape of every tool, OR keep it inputSchema-only? Recommend: **document-response shape too**, lands in PR-1 alongside the CI ack-check. Closes a v1.0 design gap; small change to `test_v1_schema_snapshot.py`.
6. **PR description templates filenames** — `feat-runner.md` + `feat-tool.md` + `feat-bookend.md` + `release.md` (proposal — covers the four patterns this repo actually uses) vs single `default.md`? Recommend: **four specific templates** — GitHub picks the right one based on PR-creation flow when multiple templates exist; better UX than a single generic.

---

## 11. Decisions Ratified

Locked 2026-06-03:

1. **`multipart/form-data` framing** — `{"image": bytes}` per spec §4.
2. **Strict JSON response contract** — `{"detections": [{"label", "bbox", "score"}, ...]}` only; clear `malformed_json` error otherwise. v1.x can add an adapter mode additively later.
3. **`/health` URL derived from inference endpoint** — replace trailing `/infer` with `/health`. Keeps env-var count low. Documented convention.
4. **`QA_INFERENCE_TIMEOUT_S` default 10 s** — fast feedback during dev; users tune up for batch.
5. **Snapshot ack expands to lock documented-response shape** in PR-1 alongside the CI ack-check. Closes a v1.0 design gap.
6. **Four PR description templates** — `feat-runner.md`, `feat-tool.md`, `feat-bookend.md`, `release.md`. GitHub auto-presents the chooser when multiple templates exist.

---

## 12. Process Invariants Honored

From `docs/v1.2-planning.md` §5 (cumulative from v0.10 + v0.11 + v1.1):

1. ✅ Mini-PRD before action — this document, before PR-1.
2. ✅ Dogfood against real artifacts — optional Phase 3 dogfood step uses a stub Flask service.
3. ✅ Version-sync invariant — PR-4 bumps pyproject + manifests; soft semver test enforces ≥ floor.
4. ✅ PyPI Summary 512-char limit — PR-4's description tweak will pass through the regression test.
5. ✅ Spike validates produced VALUE — PR-2's tests assert real `requests.post` invocation with the right framing, not just rc=0.
6. ✅ Consent gates document themselves — no new consent gates introduced.
7. ✅ Tool-count refs sync test — v1.0 PR-2 mechanism unchanged. v1.2 keeps the tool surface at 22.
8. ✅ Soft version-pin tests — floor stays at (1, 0, 0).
9. ✅ **CI workflow ack-check** — landing in PR-1. Closes 3-postmortem-old gap.
10. ✅ **PR description templates** — landing in PR-1 alongside the ack-check.
11. ✅ **Knowledge-section runner-aware selection** — landing in PR-4 as the first real `init_qa_knowledge` consumer of `QA_RUNNER`.

---

*End of mini-PRD v0.1 for mk-qa-master v1.2.0. Cross-references: `docs/v1.2-planning.md` (strategic context, Phase 3 primary), `docs/prd-v1.1-edge-ai-runner.md` (Phase 1+2 contract that this builds on additively), `docs/MIGRATION-1.x.md` (v1.x additive change log — entry to be added in PR-4), `docs/DEPRECATION-POLICY.md` (formal cycle), `mk-qa-master-edge-ai-enhancement.md` (build-ready Edge AI spec — Phase 3 sections continue to apply).*
