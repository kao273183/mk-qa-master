# mk-qa-master v1.3.0 — Edge AI Runner Phase 4 (Resilience + Coach)

**Status:** Draft v0.1 · **Author:** Jack Kao · **Date filed:** 2026-06-03 · **Successor to:** v1.2.1 (relicense announcement) · **Theme picked:** v1.3 planning §3 (Phase 4 alone as v1.3.0)

Mini-PRD (12 sections, same shape as `prd-v1.0-stability-lock.md`, `prd-v1.1-edge-ai-runner.md`, `prd-v1.2-edge-ai-phase-3.md`). v1.3 planning §3 ratified Phase 4 alone (no Theme C bundle). All 6 §6 decisions ratified with default selections in §11.

Build-ready technical reference: `mk-qa-master-edge-ai-enhancement.md` §11 (Optimizer + flake signals) + §12 (Resilience artifacts). The v1.1 / v1.2 implementations followed §1–§10 of that spec; v1.3 finishes §11–§12.

---

## 1. Vision

> **Edge AI testing's final chapter: when the network jitters, the stream drops, or the codec corrupts, the suite catches it.**

v1.1 made Edge AI runnable. v1.2 made it talk to real boards. v1.3 makes it **honest about real-world conditions**.

After v1.3.0, a QA engineer can:
1. Generate a resilience-mode test alongside the normal detection/throughput pair
2. Run it with `QA_EDGE_NETEM_ENABLED=true` on Linux CI
3. Get assertions that fire when the model degrades under jitter / loss / mid-stream disconnect / corrupted GOPs
4. See those failures categorized by `get_optimization_plan` with Edge-specific priority (🔴 high for SLA breach, 🟡 medium for variance/jitter)
5. Read the `edge_metrics` block in `report.json` to know what specifically broke

This closes the Edge AI Phase arc cleanly. v2.0 ships with the full Edge AI story locked into the new (Apache 2.0) surface.

---

## 2. Problem Statement

User-side: Edge AI runs in CI pass on a dedicated Linux runner with a clean network — perfect conditions — and produce green reports. Production deployments hit dropped frames, transient network jitter, occasional bad GOPs from camera firmware quirks. The CI suite says "fine"; production says "🔴 detection rate fell off a cliff yesterday at 03:00 UTC". The signal-to-action gap is what Phase 4 closes.

Architecturally: v1.1 + v1.2 covered *capability* (run YOLO over RTSP, talk to a Jetson). Phase 4 covers *robustness* (does the capability survive degradation). The optimizer surface (`get_optimization_plan`, stable since v0.5) is the natural place to surface "your Edge tests are flaking under condition X" alongside the existing flake-signal categories.

Zero new MCP tools. Zero new runners. One documented response-shape addition (`edge_metrics` in `get_test_report`). v1.0 stability lock honored.

---

## 3. MVP Scope (v1.3.0)

**In scope:**

- New module `src/mk_qa_master/edge/resilience.py`:
  - `apply_netem(jitter_ms=80, loss_pct=2)` — sets up `tc qdisc add dev lo root netem` (Linux only)
  - `clear_netem()` — tears down the qdisc
  - `kill_ffmpeg_subprocess(handle, after_seconds=5)` — schedules a mid-stream kill on the SourceHandle's ffmpeg process
  - `build_corrupted_gop_fixture(input_path, output_path, corrupt_at_second=3)` — ffmpeg invocation that produces a clip with corrupted GOP starting at the configured second
- Edge flake signals in `get_optimization_plan`'s suite-quality lens:
  - `latency_p95_exceeded_sla` (🔴 high) — fires when p95 latency > `EDGE_LATENCY_SLA_MS`
  - `fps_variance_across_runs` (🟡 medium) — fires when fps stddev > 20% across last 5 runs
  - `iou_jitter_per_tc` (🟡 medium) — fires when same-tc IoU stddev > 0.1 across last 5 runs
  - `coverage_gap_per_label` (🟡 medium) — fires when annotation has label X but no test exists for it
- New optional `edge_metrics` field in `report.json` (per-test): `{p95_latency_ms, fps, iou_per_frame: [...]}`. Documented in the schema; absent when the runner isn't `edge`.
- `generate_test` edge template extension: optional `resilience_mode` parameter that injects a `pytest.fixture` setup calling `apply_netem()` before the test + `clear_netem()` after
- New env var `QA_EDGE_NETEM_ENABLED` (default `false`) — gates the resilience module's netem-touching paths. When unset, helpers raise a clear error rather than silently no-oping
- Doc updates: README troubleshooting block gains 3 new entries (Linux-only / `QA_EDGE_NETEM_ENABLED` reminder / corrupted-GOP fixture regen); MIGRATION-1.x v1.2.1 → v1.3.0 entry covering the additive surface changes
- Theme J sweep: add `analyze_stream` to `skills/mk-qa-master/reference/tool-surface.md`'s "stable" list (~5 min)

**Explicitly out of scope (deferred to v2.0+):**

- Theme C: YAML config UX (~3-4 days; v2.0 candidate)
- Theme E: OWASP API4 rate limit (~2-3 days; v1.4 candidate)
- Apache 2.0 LICENSE file replacement (v2.0 work per v1.2.1 announcement)
- `EDGE_TEST_TEMPLATE` migration to `templates/edge/*.tmpl` files (v2.0 cleanup per v1.1 postmortem §9 #5)
- macOS / Windows netem polyfill (Phase 4's resilience injection is Linux-only by design — corresponding pytest skip on other OS)
- Real-camera resilience scenarios (proprietary cameras with reconnect logic — out of v1.x scope)

**v1.3.0 timeline:** **~4 working days** of code + ~0.5d for the Theme J sweep + 0.5d release. 4 PRs.

---

## 4. Architecture

### `mk_qa_master.edge.resilience` module

```python
# src/mk_qa_master/edge/resilience.py
"""v1.3.0 — Resilience injection helpers for Edge runner generated tests.

All three helpers are Linux-only (netem uses tc qdisc which only exists
on Linux). Calling them on macOS / Windows raises RuntimeError with a
clear message — generated tests should pytest.skip-guard the call.

QA_EDGE_NETEM_ENABLED must be true for apply_netem() to fire; otherwise
the helper raises RuntimeError. Prevents accidental jitter injection
during normal `QA_RUNNER=edge` runs.
"""
import os
import subprocess
from typing import Any


def _linux_only(operation: str) -> None:
    import sys
    if sys.platform != "linux":
        raise RuntimeError(
            f"{operation} requires Linux (tc qdisc / netem). "
            "Skip the test on macOS/Windows via pytest.mark.skipif."
        )


def _netem_enabled() -> None:
    if os.getenv("QA_EDGE_NETEM_ENABLED", "").lower() not in ("1", "true", "yes"):
        raise RuntimeError(
            "Resilience injection requires QA_EDGE_NETEM_ENABLED=true. "
            "This guard prevents accidental jitter during normal runs."
        )


def apply_netem(jitter_ms: int = 80, loss_pct: int = 2) -> None:
    """Add a netem qdisc to the loopback interface — affects local RTSP."""
    _linux_only("apply_netem")
    _netem_enabled()
    subprocess.run(
        ["tc", "qdisc", "add", "dev", "lo", "root", "netem",
         "delay", f"{jitter_ms}ms", "loss", f"{loss_pct}%"],
        check=True,
    )


def clear_netem() -> None:
    """Tear down the qdisc — safe to call even when nothing's set."""
    _linux_only("clear_netem")
    subprocess.run(
        ["tc", "qdisc", "del", "dev", "lo", "root"],
        check=False,  # best-effort teardown
    )


def kill_ffmpeg_subprocess(handle: Any, after_seconds: int = 5) -> None:
    """Schedule a mid-stream ffmpeg kill via a background thread.
    handle is a SourceHandle from edge/rtsp_source.py."""
    import threading
    def _kill():
        import time
        time.sleep(after_seconds)
        for p in handle._procs:
            try:
                p.terminate()
            except Exception:
                pass
    threading.Thread(target=_kill, daemon=True).start()


def build_corrupted_gop_fixture(
    input_path: str, output_path: str, corrupt_at_second: int = 3
) -> None:
    """Produce a clip with a corrupted GOP starting at corrupt_at_second.
    Used to verify the test catches frame-decode failures rather than
    crashing with a Python-level exception.

    Uses ffmpeg's bitstream-injection filter to overwrite a portion of
    the GOP. The output is small (matches input duration); cv2 will
    return ok=False for affected frames."""
    _linux_only("build_corrupted_gop_fixture")
    subprocess.run(
        ["ffmpeg", "-y", "-i", input_path,
         "-vf", f"select='between(t,{corrupt_at_second},{corrupt_at_second + 1})*0+1'",
         "-c:v", "libx264", "-preset", "ultrafast",
         "-bsf:v", "noise=10000",  # inject noise into the bitstream
         output_path],
        check=True,
    )
```

### Edge flake signals in `get_optimization_plan`

The optimizer reads the last 5 archived `report.json` files. For each `edge_metrics` block found, it computes 4 signals:

| Signal | Priority | Triggers when |
|---|---|---|
| `latency_p95_exceeded_sla` | 🔴 high | `p95_latency_ms > EDGE_LATENCY_SLA_MS` in current run |
| `fps_variance_across_runs` | 🟡 medium | `stddev(fps) / mean(fps) > 0.2` across last 5 runs |
| `iou_jitter_per_tc` | 🟡 medium | `stddev(iou_per_frame for same nodeid) > 0.1` across last 5 runs |
| `coverage_gap_per_label` | 🟡 medium | annotations contain label X but no test mentions X in its name/description |

Each signal emits a line into the existing optimizer's recommendation block — same format other runners use; no new schema.

### `generate_test` resilience-mode flag

```python
runner.generate_test(
    description="detect person under jitter",
    filename="test_edge_jitter_person.py",
    annotations_path="...",
    label="person",
    resilience_mode="netem",  # NEW — emits apply_netem() / clear_netem() fixture
)
```

Generated test gains a session-scoped fixture that wraps the existing detection test:

```python
@pytest.fixture(scope="session", autouse=True)
def _resilience():
    pytest.importorskip("mk_qa_master.edge.resilience")
    from mk_qa_master.edge.resilience import apply_netem, clear_netem
    apply_netem(jitter_ms=80, loss_pct=2)
    yield
    clear_netem()
```

`pytest.importorskip` + `_linux_only` guards together ensure the test skips cleanly on non-Linux without manual `skipif` chains.

---

## 5. Surface Area Implications (v1.0 stability lock paperwork)

v1.3.0 triggers the `BREAKING_CHANGE_ACK=true` mechanism for the third time (v1.1.0 was first; v1.2.0's `init_qa_knowledge` response shape change technically didn't fire because snapshot is inputSchema-only):

1. **Snapshot test update**: `get_test_report`'s response gains an optional `edge_metrics` field per-test. The current snapshot file (`tests/snapshots/v1/tool_surface.json`) doesn't lock response shape. v1.2.0 PR-1's expansion (description-text response-shape lock) covers bookend tools; we should consider extending it to also assert `edge_metrics` is documented in the `get_test_report` tool description. **Decision in §11 #2**: add this check, fires snapshot ack.

2. **Update `docs/MIGRATION-1.x.md`** with a v1.2.1 → v1.3.0 entry covering:
   - `mk_qa_master.edge.resilience` module (importable; helpers are Linux-only)
   - `edge_metrics` optional field in `report.json` per-test
   - 4 new Edge flake signals in `get_optimization_plan` output
   - New `QA_EDGE_NETEM_ENABLED` env var
   - `generate_test`'s new `resilience_mode` parameter

3. **Update README env-var table** with `QA_EDGE_NETEM_ENABLED`
4. **Update SKILL.md** — Edge AI section mentions resilience-mode tests
5. **Bump pyproject + 2 plugin manifests** `1.2.1 → 1.3.0`
6. **CI ack-check workflow step** (already in production since v1.2.0 PR-1) — automatically fires when we set `BREAKING_CHANGE_ACK=true`

### Theme J sweep (per v1.3 planning §3 secondary recommendation)

Single small addition to `skills/mk-qa-master/reference/tool-surface.md` — add `analyze_stream` to the "Stable since v1.1" list. ~5 minutes; bundled into PR-4.

---

## 6. Consent / Safety

- **`QA_EDGE_NETEM_ENABLED=true` is a real consent gate.** Without it, `apply_netem()` raises `RuntimeError`. Honors the v0.7 / v0.8 pattern of consent-required side effects (netem affects ALL loopback traffic on the host, not just the test's RTSP stream — could disrupt other processes).
- **No new MCP-level consent gates.** `run_tests` already gates on `QA_VISUAL_CHALLENGE_CONSENT` etc.; Edge resilience is runner-internal.
- **Vendor-host blacklist unchanged** — Phase 4 doesn't touch analyze_stream's input.
- **Telemetry hygiene**: failures from `apply_netem()` include OS / sysadmin context but no PII, no RTSP URL. The optimizer's flake signal output names test nodeids but not their RTSP URLs.

---

## 7. Tests

### PR-1 (`mk_qa_master.edge.resilience` module)

```
tests/test_edge_resilience.py:
  test_apply_netem_calls_tc_qdisc_add_with_correct_args
  test_apply_netem_raises_when_QA_EDGE_NETEM_ENABLED_unset
  test_apply_netem_raises_on_non_linux
  test_clear_netem_tears_down_quietly_when_nothing_set
  test_kill_ffmpeg_subprocess_schedules_terminate
  test_build_corrupted_gop_fixture_invokes_ffmpeg_with_noise_filter
  test_corrupted_gop_fixture_raises_on_non_linux
```

`subprocess.run` mocked via `unittest.mock.patch`; no real `tc` / `ffmpeg` calls in unit tests.

### PR-2 (Edge flake signals in optimizer)

```
tests/test_optimizer_edge_signals.py:
  test_latency_p95_signal_fires_when_exceeds_sla
  test_fps_variance_signal_uses_relative_stddev_threshold
  test_iou_jitter_per_tc_aggregates_across_runs
  test_coverage_gap_per_label_compares_annotations_to_test_names
  test_signals_absent_when_runner_not_edge
```

### PR-3 (`generate_test` resilience-mode flag)

```
tests/test_edge_runner.py — extend:
  test_generate_test_emits_apply_netem_fixture_when_resilience_mode_is_netem
  test_generate_test_omits_resilience_fixture_by_default
```

### PR-4 (release)

```
tests/test_init_qa_knowledge_runner_aware.py — extend (small):
  test_init_response_includes_edge_metrics_pointer_when_runner_is_edge
  (smoke for the new Migration entry's reference)
```

### v1.0 contract enforcement

- `tests/test_v1_schema_snapshot.py` — flagged for ack on PR-4 if we add the `edge_metrics`-in-description check per §11 #2
- `tests/test_v1_doc_sync.py` — catches stale env-var table / tool count
- `tests/test_v1_deprecation_policy.py` — no-op (no deprecations)

### CI

The `edge-sample` job from v1.1.1 / v1.2.0 gets a new conditional step in PR-4: when `RUNNER_OS=Linux` AND the resilience tests are tagged, run them with `QA_EDGE_NETEM_ENABLED=true` against the bundled fixture. macOS / Windows still skip cleanly via `_linux_only` guards.

---

## 8. Implementation Plan

| PR | Days | Scope | Triggers ack? |
|---|---|---|---|
| PR-1 | 1.5 | `mk_qa_master.edge.resilience` module + 7 unit tests (subprocess mocked) | No (runner-internal helpers) |
| PR-2 | 1 | Edge flake signals in `get_optimization_plan` + history archive `edge_metrics` per-test field + 5 tests | No (runner-internal computation) |
| PR-3 | 0.5 | `generate_test` resilience-mode flag + 2 tests | No (additive parameter) |
| PR-4 | 1 | Snapshot-test response-shape lock expansion (per §11 #2) + MIGRATION-1.x entry + README/SKILL sync + Theme J sweep (analyze_stream → stable list) + bump 1.2.1 → 1.3.0 + tag + release | **YES** (description-text check fires on `get_test_report`'s `edge_metrics` mention) |
| **Total** | **~4 days** | | |

PR-1 lands the helpers. PR-2 makes them visible in the optimizer output. PR-3 makes them callable from generated tests. PR-4 wraps + releases.

---

## 9. Roadmap Context

This PRD lives at `docs/prd-v1.3-edge-ai-phase-4.md`. After v1.3.0 ships:

- `docs/v1.3-planning.md` §8 gets a postmortem section
- v1.4 planning opens with Theme E (OWASP API4 rate limit) as primary recommendation
- v2.0 planning opens after v1.4 ships; Theme C (YAML config) + Apache 2.0 relicense bundle as the v2.0 main work
- v1.3 → v2.0 timing: **strict per §11 #6** — v1.3 ships in 2 weeks of merge, then v2.0 follows after at least 1 calendar month minimum (so the v1.x → v2.x hold cycle is observed cleanly)

v1.3 explicitly **does not** include:
- Theme C (YAML config UX) — v2.0 candidate (bundled with Apache 2.0)
- Theme E (OWASP API4 rate limit) — v1.4
- Apache 2.0 LICENSE file — v2.0 only
- `EDGE_TEST_TEMPLATE` template-file migration — v2.0 cleanup
- macOS/Windows netem polyfill — out of scope

---

## 10. Decisions Required Before Coding

1. **Phase 4 + Theme C bundling** — Phase 4 alone (proposal — v1.3 stays focused) vs bundle YAML config? Recommend: **alone**. Bundling YAML adds a backward-compat surface that competes for review attention with the ack-triggering snapshot work.
2. **`edge_metrics` response key naming** — `edge_metrics` (proposal — descriptive, scoped to edge runner) vs `runner_metrics` (extensible to other runners later) vs `extra` (generic)? Recommend: **`edge_metrics`** — explicit, the right scope for now. If other runners ever need it, additive field next to `edge_metrics` works.
3. **`netem` defaults** — `QA_EDGE_NETEM_ENABLED=true` opt-in (proposal — safe; matches the v0.7/v0.8 consent pattern) vs auto-enabled when Linux + `QA_RUNNER=edge`? Recommend: **opt-in**. netem affects all loopback traffic; quiet enabling could disrupt unrelated processes.
4. **Corrupted-GOP fixture** — generate-on-demand via ffmpeg in CI (proposal — stays out of binary fixture cap) vs commit a pre-made one? Recommend: **generate-on-demand**. The corrupted variant is derived from `factory.mp4`; committing both means doubling the fixture cost.
5. **Theme J sweep scope** — only `tool-surface.md` Edge addition (proposal — ~5 min) vs include `EDGE_TEST_TEMPLATE` move? Recommend: **`tool-surface.md` only**. Template-file migration is real refactor work; defer to v2.0 cleanup batch.
6. **v1.3 → v2.0 timing** — strict "v1.3 ships in 2 weeks of merge, v2.0 follows after ≥ 1 calendar month minimum" (proposal — honors the announce-then-hold cycle from v1.2.1) vs open-ended? Recommend: **strict**. The hold cycle's whole point is letting downstream users react; arbitrary timing erodes the signal.

---

## 11. Decisions Ratified

Locked 2026-06-03:

1. **Phase 4 alone as v1.3.0** — no Theme C bundle. Honors the "v1.3 closes Edge AI arc cleanly" rationale.
2. **Response key name = `edge_metrics`** — scoped, descriptive. v1.x can add `<other>_metrics` peer fields additively.
3. **`QA_EDGE_NETEM_ENABLED=true` opt-in** — netem affects all loopback traffic; quiet enabling = silent disruption. Default disabled.
4. **Corrupted-GOP fixture generated on-demand** in CI (and locally by users) via ffmpeg invocation against the committed `factory.mp4`. No new committed binary.
5. **Theme J sweep limited to `tool-surface.md` `analyze_stream` addition** — ~5 min. `EDGE_TEST_TEMPLATE` and README pass held for v2.0.
6. **v1.3 → v2.0 timing strict**: v1.3 ships within 2 weeks of merge; v2.0 follows after ≥ 1 calendar month minimum after v1.3 ships. Documented in MIGRATION-1.x v1.2.1 → v1.3.0 entry.

---

## 12. Process Invariants Honored

From `docs/v1.3-planning.md` §5 (cumulative from v0.10 + v0.11 + v1.1 + v1.2):

1. ✅ Mini-PRD before action — this document, before PR-1.
2. ✅ Dogfood against real artifacts — PR-1's `edge-sample` CI gets a `netem`-conditional run on Linux.
3. ✅ Version-sync invariant — PR-4 bumps pyproject + manifests; soft semver test enforces ≥ floor.
4. ✅ PyPI Summary 512-char limit — PR-4's description tweak will pass through the regression test.
5. ✅ Spike validates produced VALUE — PR-1's tests assert `tc qdisc` is called with the right args, not just rc=0.
6. ✅ Consent gates document themselves — `QA_EDGE_NETEM_ENABLED` documented in README + MIGRATION-1.x + the helper's error message.
7. ✅ Tool-count refs sync test — v1.0 PR-2 mechanism unchanged. v1.3 keeps the tool surface at 22.
8. ✅ Soft version-pin tests — floor stays at (1, 0, 0).
9. ✅ CI workflow ack-check — already in production; PR-4 sets `BREAKING_CHANGE_ACK=true` and the workflow validates the paired MIGRATION edit lands in the same PR.
10. ✅ PR description templates — pick `feat-runner.md` for PR-1 + PR-2 + PR-3, `release.md` for PR-4.
11. ✅ Knowledge-section runner-aware selection — `init_qa_knowledge`'s flag (added v1.2.0) flips appropriately for edge runner users.

---

*End of mini-PRD v0.1 for mk-qa-master v1.3.0. Cross-references: `docs/v1.3-planning.md` (strategic context, Phase 4 primary), `docs/prd-v1.2-edge-ai-phase-3.md` (Phase 3 contract this builds on additively), `docs/MIGRATION-1.x.md` (v1.x additive change log — entry to be added in PR-4), `docs/DEPRECATION-POLICY.md` (formal cycle + license-change section), `docs/RELICENSING.md` (v2.0 license transition plan), `mk-qa-master-edge-ai-enhancement.md` (build-ready Edge AI spec — §11 + §12 apply directly).*
