"""v0.8 spike — measure real Maestro CLI wall-clock cost.

⚠️  HISTORICAL — v0.8.0 mobile work was parked 2026-05-26. See
    docs/v0.8-mobile-postmortem.md for the architectural blocker
    that ended the effort. Specifically: this script's `runScript`
    flow only asserts rc == 0; it never reads `output.dpr` back.
    The JS errored silently in Maestro's GraalJS sandbox (no
    `window` available), but rc stayed 0, so the spike reported
    success. The **latency findings remain valid**; the inferred
    "runScript reads WebView DOM" capability does NOT — runScript
    runs in a Kotlin-side sandbox, not in the device WebView.

The v0.8 PRD assumes each tap / runScript / screenshot is a separate
`maestro test` subprocess invocation, since the CLI primitives we want
(tap-at-coords, eval-JS) only exist as YAML flow commands — not as
direct CLI flags. This spike pins down the real latency before we
ratify the v0.8 timeline.

Output guides three downstream decisions:

  1. Is the per-operation cost < 500ms?
     → YES: ship the simple driver layer as designed in v0.8 PRD §4
     → NO:  batched-flow optimization moves from v0.8.1 → v0.8.0 MVP

  2. Does YAML generation / temp-file mgmt blow up under load?
     → Run 20 sequential ops, watch for monotonic creep / handle leaks

  3. Does Maestro studio / device session warm up after first invocation?
     → Compare op #1 (cold) vs op #2-N (warm). If warm is much faster,
       we want to keep the device session alive between ops somehow.

Prereqs:
  - Maestro CLI installed (`brew install maestro`)
  - iOS Simulator OR Android Emulator OR real device booted with any app
  - Env var SPIKE_APP_ID set to the foreground app's bundle id
    (e.g., SPIKE_APP_ID=com.apple.mobilesafari for iOS Safari)
"""
from __future__ import annotations

import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path


def _have_maestro() -> bool:
    return shutil.which("maestro") is not None


def _run_flow(yaml_body: str, *, timeout_s: float = 120.0) -> tuple[float, int, str]:
    """Write a one-shot YAML flow, run `maestro test`, return (wall_s, rc, stderr_tail)."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as f:
        f.write(yaml_body)
        path = f.name
    try:
        t0 = time.monotonic()
        proc = subprocess.run(
            ["maestro", "test", path],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        wall = time.monotonic() - t0
        return wall, proc.returncode, (proc.stderr or "")[-400:]
    finally:
        try:
            Path(path).unlink()
        except OSError:
            pass


def _measure(label: str, yaml_body: str, *, iterations: int = 5) -> None:
    print(f"\n=== {label} ===")
    print(f"  YAML body:")
    for line in yaml_body.rstrip().splitlines():
        print(f"    {line}")
    times: list[float] = []
    for i in range(iterations):
        wall, rc, err = _run_flow(yaml_body)
        flag = "OK" if rc == 0 else f"FAIL rc={rc}"
        print(f"  run {i + 1}/{iterations}: {wall * 1000:.0f} ms  [{flag}]")
        if rc != 0 and err:
            print(f"    stderr tail: {err[-200:]!r}")
        times.append(wall)
    print(f"  → mean {statistics.mean(times) * 1000:.0f} ms / "
          f"min {min(times) * 1000:.0f} / max {max(times) * 1000:.0f} / "
          f"stdev {statistics.stdev(times) * 1000 if len(times) > 1 else 0:.0f}")


def main() -> int:
    if not _have_maestro():
        print("ERROR: `maestro` not on PATH. Install: brew install maestro",
              file=sys.stderr)
        return 2

    app_id = os.getenv("SPIKE_APP_ID")
    if not app_id:
        print(
            "ERROR: SPIKE_APP_ID env var required. Example:\n"
            "  SPIKE_APP_ID=com.apple.mobilesafari python scripts/spike-maestro-perf.py",
            file=sys.stderr,
        )
        return 2

    print(f"Maestro version:")
    subprocess.run(["maestro", "--version"], capture_output=True, text=True)
    print(f"Target app id: {app_id}")

    # Warm-up: drive `maestro hierarchy` once so the iOS driver gets
    # installed / launched. Without this, the first real flow eats the
    # ~30s driver-install cost and skews the per-tap mean.
    print("\n=== WARM-UP — maestro hierarchy (one-time iOS driver init) ===")
    t0 = time.monotonic()
    warm = subprocess.run(
        ["maestro", "hierarchy"], capture_output=True, text=True, timeout=180
    )
    print(f"  warm-up wall: {(time.monotonic() - t0) * 1000:.0f} ms, rc={warm.returncode}")

    # 1. Pure tap-at-coord
    _measure(
        "TAP @ (50%, 50%) — single tap, no other ops",
        textwrap.dedent(f"""\
            appId: {app_id}
            ---
            - tapOn:
                point: "50%, 50%"
        """),
    )

    # 2. Eval JS in WebView (requires a WebView in foreground; skipped if app lacks one)
    _measure(
        "RUNSCRIPT — eval JS that returns devicePixelRatio",
        textwrap.dedent(f"""\
            appId: {app_id}
            ---
            - runScript: |
                output.dpr = window.devicePixelRatio;
                output.viewport = window.innerWidth + 'x' + window.innerHeight;
        """),
    )

    # 3. Screenshot
    _measure(
        "SCREENSHOT — full device screenshot to /tmp",
        textwrap.dedent(f"""\
            appId: {app_id}
            ---
            - takeScreenshot: spike_shot
        """),
    )

    # 4. Combined: tap + screenshot in ONE flow (batched)
    _measure(
        "BATCHED — tap @ (50%, 50%) + screenshot in same flow",
        textwrap.dedent(f"""\
            appId: {app_id}
            ---
            - tapOn:
                point: "50%, 50%"
            - takeScreenshot: spike_batched
        """),
    )

    print(
        "\n"
        "Interpretation guide:\n"
        "  - If single-tap mean < 500 ms → v0.8 PRD §4 simple driver design holds.\n"
        "  - If single-tap mean 500-1500 ms → flag in v0.8 §10, consider batching.\n"
        "  - If single-tap mean > 1500 ms → batched-flow optimization required for MVP.\n"
        "  - If batched / single ratio < 1.5x → batching saves nothing, skip it.\n"
        "  - If runscript fails consistently → token-readback strategy needs rethink.\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
