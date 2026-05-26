"""Driver layer for the Visual Challenge Solver (v0.8.0 PRD §4).

v0.7.x had Playwright calls hardcoded inline in visual_challenge.py.
v0.8 introduces a driver Protocol so a Maestro-backed implementation
can plug in for mobile WebView solves without rewriting the orchestration
logic in visual_challenge.py.

Rollout history:

  PR 2 — Protocol + PlaywrightDriver thin facade that delegates to the
         existing module-level functions in visual_challenge.py.
  PR 3 — extracted per-operation methods (find_iframe / iframe_bbox /
         cell_bbox / click_at / ...) into PlaywrightDriver and routed
         _detect_visual_challenge through them.
  PR 4 (this commit) — MaestroDriver.detect_challenge in mega-YAML
         mode. Generates one YAML flow per inspect cycle, runs
         `maestro test`, parses the runScript output for cell bboxes
         and challenge text. execute_solve still raises
         NotImplementedError; PR 5 wires it up.
  PR 5+ — MaestroDriver.execute_solve mega-YAML + sample mobile fixture
         + Tier 1/2/3 CI workflows.

The PRD lives at docs/prd-v0.8-mobile-webview-captcha.md. §11 ratifies
all seven design decisions including mega-YAML mode for MaestroDriver.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class VisualChallengeDriver(Protocol):
    """Page-driving layer for inspect/solve.

    Two implementations land across v0.8.x:

      - PlaywrightDriver: drives a Playwright `page` object directly.
        Operations run in-process; each call is ~10 ms. Used for
        desktop captcha solving (v0.7 path).

      - MaestroDriver (v0.8.0 step 3): assembles a mega-YAML flow and
        invokes `maestro test` once per solve round. Per-op cost is
        amortized inside the YAML. Used for mobile WebView captcha
        solving — iOS Simulator / Android Emulator / real device.

    Methods are kept high-level so the MaestroDriver mega-YAML mode can
    accumulate ops internally and commit them in one subprocess; a
    finer-grained per-Playwright-API interface would force MaestroDriver
    to spawn one subprocess per call (~30 s each) and make multi-round
    solves take 9 minutes. See PRD §4 spike data.
    """

    def detect_challenge(
        self, selector_override: str | None = None
    ) -> dict[str, Any] | None:
        """Locate a CAPTCHA iframe, screenshot it, extract tile geometry.

        Returns the same shape `_detect_visual_challenge()` has returned
        since v0.7.0:

            {
              "screenshot_base64": <str, data:image/png;base64,...>,
              "challenge_text":    <str>,
              "grid_layout":       "3x3" | "4x4",
              "tile_count":        9 | 16,
              "tiles":             [{index, viewport_x, viewport_y, w, h}, ...],
              "fingerprint":       <"recaptcha-v2-image-3x3" | ...>,
              "fingerprint_id":    <"recaptcha-v2-image" | "hcaptcha-image">,
              "fingerprint_config": <fingerprint table entry>,
              "frame_locator":     <driver-specific handle for the iframe>,
              "_coord_method":     "per_cell_bbox" | "table_rect_js" | "iframe_divide",
            }

        Returns None when no fingerprint matches (no CAPTCHA on the
        current page).
        """
        ...

    def execute_solve(
        self,
        record: Any,
        selected_tile_indices: list[int],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        """Click each selected tile, click Verify, wait for token.

        `record` is a `_ChallengeRecord` from visual_challenge.py — it
        carries the driver-specific iframe handle plus the fingerprint
        config for selector lookup. Drivers should treat `record.page`
        / `record.frame_locator` as opaque (the field types differ
        between PlaywrightDriver and MaestroDriver).

        Returns the same shape `_execute_solve()` has returned since
        v0.7.0:

            {
              "status":              "passed" | "failed" | "continue" | "error" | ...,
              "challenge_id":        <str>,
              "attempts_remaining":  <int>,
              "token":               <str | None>,
              "hint":                <str>,
              ...                    (optional fields per status)
            }
        """
        ...


class PlaywrightDriver:
    """Drives Playwright in-process — the v0.7.x execution path.

    PR 2 (this file's initial form): thin facade that delegates to the
    existing module-level functions in visual_challenge.py. The actual
    Playwright API calls remain inline in `_detect_visual_challenge()`
    and `_execute_solve()` for now.

    PR 3 will move those API calls into per-operation methods on this
    class (find_iframe / iframe_bbox / cell_bbox / click_at / ...) so
    `_detect_visual_challenge()` becomes driver-agnostic orchestration
    code. The Protocol surface above stays the same — PR 3 is a private
    refactor.
    """

    def __init__(self, page: Any):
        # `page` is a Playwright sync Page object (or a MagicMock shaped
        # like one in unit tests). Stored opaquely; PR 3 will start
        # methods that wrap individual page.* calls so call sites stop
        # touching this attribute directly.
        self._page = page

    def detect_challenge(
        self, selector_override: str | None = None
    ) -> dict[str, Any] | None:
        # Local import dodges the circular dependency: visual_challenge.py
        # imports this module for the Driver protocol, and we delegate
        # back to its detection function. The function now (v0.8.0 PR 3)
        # takes the driver itself rather than a page, so each inline
        # Playwright call from v0.7.x routes through the per-op methods
        # below.
        from . import visual_challenge as _vc

        return _vc._detect_visual_challenge(
            self, selector_override=selector_override
        )

    def execute_solve(
        self,
        record: Any,
        selected_tile_indices: list[int],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        from . import visual_challenge as _vc

        return _vc._execute_solve(
            self,
            record,
            selected_tile_indices,
            timeout_seconds=timeout_seconds,
        )

    # ---- Per-operation methods (PR 3 — replaces inline Playwright calls
    # in _detect_visual_challenge / _execute_solve)
    #
    # Each method is intentionally defensive: catches the broad Playwright
    # exception space and returns a sentinel (None / "" / 0 / False / b"")
    # so callers in visual_challenge.py can write straight-line code
    # without try/except scaffolding around every locator call. The
    # legacy functions already swallowed these exceptions; the methods
    # below preserve that behavior verbatim.
    #
    # MaestroDriver (v0.8.0 step 3) will implement the same methods,
    # accumulating ops in a YAML buffer and committing them on a
    # subsequent commit_batch() call (added then).

    # --- Iframe-level (page scope) ----------------------------------------

    def find_iframe(self, selector: str) -> Any | None:
        """Return first iframe element matching `selector`, or None."""
        try:
            locator = self._page.locator(selector)
            count = locator.count() if hasattr(locator, "count") else 0
        except Exception:
            return None
        if count and count > 0:
            return locator.first
        return None

    def element_bbox(self, element: Any) -> dict | None:
        """Return `{x, y, width, height}` of `element`, or None."""
        try:
            return element.bounding_box() or None
        except Exception:
            return None

    def element_screenshot_png(self, element: Any) -> bytes:
        """Return PNG bytes of `element`'s screenshot, or empty bytes."""
        try:
            return element.screenshot() or b""
        except Exception:
            return b""

    def frame_locator_for(self, iframe_selector: str) -> Any | None:
        """Return Playwright frame_locator for the iframe at `iframe_selector`."""
        try:
            return self._page.frame_locator(iframe_selector)
        except Exception:
            return None

    # --- Frame-level (inside iframe scope) -------------------------------

    def frame_count(self, frame: Any, selector: str) -> int:
        """Count of elements matching `selector` inside `frame`."""
        try:
            return frame.locator(selector).count() or 0
        except Exception:
            return 0

    def frame_inner_text(self, frame: Any, selector: str) -> str:
        """Stripped `inner_text()` of first match inside `frame`. Empty
        string when no match or on error."""
        try:
            loc = frame.locator(selector)
            if loc.count() > 0:
                return (loc.first.inner_text() or "").strip()
        except Exception:
            pass
        return ""

    def frame_cell_bbox(
        self, frame: Any, selector: str, index: int
    ) -> dict | None:
        """Return `{x, y, width, height}` of the `index`-th element
        matching `selector` inside `frame`, or None."""
        try:
            return frame.locator(selector).nth(index).bounding_box() or None
        except Exception:
            return None

    def frame_evaluate_on_selector(
        self, frame: Any, selector: str, script: str
    ) -> Any:
        """Run `script` (a JS expression bound to the first matching
        element) inside `frame`. Returns whatever the script returns,
        or None on error."""
        try:
            return frame.locator(selector).evaluate(script)
        except Exception:
            return None

    def frame_click_element(self, frame: Any, selector: str) -> bool:
        """Click the first element matching `selector` inside `frame`.
        Returns True iff a click was actually issued."""
        try:
            loc = frame.locator(selector)
            if loc.count() > 0:
                loc.first.click()
                return True
        except Exception:
            pass
        return False

    # --- Page-level interactions ------------------------------------------

    def click_at(self, x: float, y: float) -> None:
        """Click at page viewport coords `(x, y)`. Re-raises on failure
        so the caller can surface a precise error message — distinct
        from the swallow-and-return semantics on the locator helpers
        because a missed coordinate click is a hard failure, not a
        no-op."""
        self._page.mouse.click(x, y)

    def page_evaluate(self, script: str) -> Any:
        """Run `script` in the page context (NOT inside an iframe).
        Returns the script's return value."""
        return self._page.evaluate(script)


# ===========================================================================
# MaestroDriver — mega-YAML mode (v0.8.0 PR 4 onwards)
# ===========================================================================
# Per the spike in docs/prd-v0.8-mobile-webview-captcha.md §4, each
# `maestro test` invocation costs ~30s fixed overhead. To stay viable,
# MaestroDriver assembles every operation needed for a single round
# (inspect OR solve) into ONE YAML and runs `maestro test` once. Per-op
# cost amortizes to ~200ms inside the flow.
#
# Trade-off: the AI client returns a tile selection once per round and
# waits ~37s for that round's mega-YAML to commit. No mid-flow
# intervention within a round. Multi-round dynamic-replace is preserved
# (each round = its own mega-YAML, AI re-judges between rounds).


def _maestro_cli_available() -> bool:
    """True when the `maestro` binary is on PATH. Cached negative result
    surfaces as a clear `no_maestro_cli` error at the tool boundary."""
    return shutil.which("maestro") is not None


# Probe JS that runs inside the WebView during the inspect mega-YAML.
# Writes everything we need to reconstruct PlaywrightDriver's detect
# return shape into `output.*` fields that Maestro exposes back.
#
# Selectors are passed in via string substitution at YAML-assembly time —
# the same per-vendor fingerprint table v0.7 uses.
#
# `output.fingerprint_id`: vendor id ("recaptcha-v2-image" / "hcaptcha-image")
#                          when iframe is present, else "" (so the driver
#                          can report no_challenge_present cleanly).
# `output.challenge_text`: stripped inner_text of the prompt selector.
# `output.tile_count`:     count of matched cell elements.
# `output.cells_json`:     JSON-stringified array of {x, y, w, h} per cell
#                          in WebView logical pixels.
# `output.viewport_w/h`:   WebView innerWidth/innerHeight for downstream
#                          DPR / percentage conversions.
_INSPECT_PROBE_JS_TEMPLATE = """\
output.fingerprint_id = "";
output.challenge_text = "";
output.tile_count = 0;
output.cells_json = "[]";
output.viewport_w = window.innerWidth;
output.viewport_h = window.innerHeight;
output.verify_x = 0;
output.verify_y = 0;
try {{
  const ifr = document.querySelector('{iframe_selector_css}');
  if (ifr) {{
    output.fingerprint_id = '{fingerprint_id}';
    let frame = ifr.contentDocument || (ifr.contentWindow && ifr.contentWindow.document);
    if (frame) {{
      const desc = frame.querySelector('{challenge_text_selector}');
      if (desc) output.challenge_text = (desc.innerText || '').trim();
      const cells = frame.querySelectorAll('{tile_cell_selector}');
      output.tile_count = cells.length;
      const arr = [];
      const ifrRect = ifr.getBoundingClientRect();
      for (const c of cells) {{
        const r = c.getBoundingClientRect();
        // Translate iframe-internal coords back to outer-page logical px.
        arr.push({{
          x: Math.round(ifrRect.left + r.left),
          y: Math.round(ifrRect.top + r.top),
          w: Math.round(r.width),
          h: Math.round(r.height),
        }});
      }}
      output.cells_json = JSON.stringify(arr);
      // v0.8.0 PR 5: capture verify button center for downstream solve
      // mega-YAML. Maestro's tapOn can't reach CSS selectors inside a
      // WebView — we tap at coordinates, so we need to know where the
      // button is right now. Re-probed every inspect; cached on the
      // ChallengeRecord until the next inspect or expiry.
      const vbtn = frame.querySelector('{verify_button_selector}');
      if (vbtn) {{
        const vr = vbtn.getBoundingClientRect();
        output.verify_x = Math.round(ifrRect.left + vr.left + vr.width / 2);
        output.verify_y = Math.round(ifrRect.top + vr.top + vr.height / 2);
      }}
    }}
  }}
}} catch (e) {{
  output.error = String(e && e.message || e);
}}
"""


def _build_inspect_yaml(
    app_id: str, probe_js_path: str, screenshot_name: str
) -> str:
    """Compose the inspect-phase mega-YAML. Three steps:
      1. launchApp (clearState: false — preserve session state across rounds)
      2. takeScreenshot — Maestro writes <name>.png to CWD
      3. runScript: probe JS file — populates output.* fields
    """
    return (
        f"appId: {app_id}\n"
        "---\n"
        "- launchApp:\n"
        "    clearState: false\n"
        f"- takeScreenshot: {screenshot_name}\n"
        f"- runScript: {probe_js_path}\n"
    )


# Token-read JS — runs after Verify tap. Synchronous: just reads the
# textarea value and returns it. Maestro's runScript is sync-only, so
# we can't poll within JS; callers handle "token not ready yet" by
# retrying the whole solve cycle.
_READ_TOKEN_JS_TEMPLATE = """\
output.token = "";
try {{
  const t = document.querySelector('{response_token_selector}');
  if (t) output.token = t.value || "";
}} catch (e) {{
  output.error = String(e && e.message || e);
}}
"""


def _build_solve_yaml(
    app_id: str,
    tile_taps: list[tuple[int, int]],
    verify_xy: tuple[int, int],
    read_token_js_path: str,
    screenshot_name: str,
    settle_ms: int = 5000,
) -> str:
    """Compose the solve-phase mega-YAML:
      1. tapOn each tile center (point: "x, y")
      2. tapOn the Verify button center
      3. waitForAnimationToEnd — let vendor JS settle + write the token
      4. takeScreenshot — for the HTML reporter / debug trail
      5. runScript: read_token.js — output.token populated if success

    All inlined into one `maestro test` subprocess; per-spike data, total
    cost ~37 s for a 5-tile round vs ~5×30 s = 2.5 min if split per-op.
    """
    lines: list[str] = [
        f"appId: {app_id}",
        "---",
    ]
    for x, y in tile_taps:
        lines.append("- tapOn:")
        lines.append(f'    point: "{x}, {y}"')
    vx, vy = verify_xy
    lines.append("- tapOn:")
    lines.append(f'    point: "{vx}, {vy}"')
    lines.append("- waitForAnimationToEnd:")
    lines.append(f"    timeout: {settle_ms}")
    lines.append(f"- takeScreenshot: {screenshot_name}")
    lines.append(f"- runScript: {read_token_js_path}")
    return "\n".join(lines) + "\n"


# Regex grabs `output.foo = bar` lines from `maestro test` stdout.
# Maestro echoes each runScript assignment under "Output:" — format is
# stable across 2.5+ but we match defensively.
_OUTPUT_LINE = re.compile(r"^\s*output\.([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*$")


def _parse_runscript_output(stdout: str) -> dict[str, str]:
    """Pull `output.*` field assignments out of `maestro test` stdout."""
    fields: dict[str, str] = {}
    for line in stdout.splitlines():
        m = _OUTPUT_LINE.match(line)
        if m:
            fields[m.group(1)] = m.group(2).strip().strip('"')
    return fields


class MaestroDriver:
    """Drives Maestro CLI in mega-YAML mode for mobile WebView CAPTCHA solving.

    Each driver instance binds to one `app_id` (the foreground app's
    bundle id / package name) and optionally one `device_id` (Maestro's
    `--udid` / `--device` value). When `device_id` is omitted, Maestro's
    auto-discovery picks the first connected device.

    The first call to `detect_challenge` writes a probe JS file to a
    tempdir, generates the inspect mega-YAML referencing it, and runs
    `maestro test` once. Subsequent calls reuse the same tempdir.

    PR 4 (this commit) implements `detect_challenge` only. `execute_solve`
    raises NotImplementedError until PR 5 wires it. The driver_not_
    implemented response from PR 1 is kept for `execute_solve` until then.
    """

    # Hard cap for the mega-YAML subprocess. Spike measured ~30 s per
    # invocation on a warm device; first-call cold can stretch to ~90 s
    # (driver install + WDA bootstrap). 180 s gives 2× headroom over
    # cold path.
    DEFAULT_SUBPROCESS_TIMEOUT_S: float = 180.0

    def __init__(
        self,
        app_id: str,
        device_id: str | None = None,
        fingerprints: list[dict[str, Any]] | None = None,
        timeout_s: float | None = None,
    ):
        if not app_id:
            raise ValueError("MaestroDriver requires an app_id (bundle id / package name)")
        self._app_id = app_id
        self._device_id = device_id
        # Fingerprints come from visual_challenge._FINGERPRINTS; passed
        # in to avoid a circular import. PR 5 will move detection of
        # which fingerprint matched into the probe JS itself; for PR 4
        # we iterate fingerprints client-side and pick the first one
        # whose iframe selector returns a match.
        self._fingerprints: list[dict[str, Any]] = fingerprints or []
        self._timeout_s = (
            float(timeout_s) if timeout_s is not None
            else self.DEFAULT_SUBPROCESS_TIMEOUT_S
        )
        # Lazy-created tempdir for probe JS files + screenshots.
        self._workdir: Path | None = None
        # DPR cache keyed by device_id (None when auto-discovery).
        # Populated on first successful inspect.
        self._dpr_cache: dict[str | None, float] = {}

    # ---- Public Protocol surface ----------------------------------------

    def detect_challenge(
        self, selector_override: str | None = None
    ) -> dict[str, Any] | None:
        """Run an inspect mega-YAML and return PlaywrightDriver-shaped output.

        Algorithm:
          1. Pick the fingerprint candidates to probe — `selector_override`
             pins to the first fingerprint; otherwise iterate
             `_FINGERPRINTS` in declared order.
          2. For each candidate, assemble the probe JS + inspect YAML
             and run `maestro test` once. The probe writes
             `output.fingerprint_id` empty when the iframe isn't on
             the current screen → move to next candidate. Non-empty →
             that vendor matched, parse the rest of the output.
          3. Return the v0.7-shape detection dict.

        Returns None when no fingerprint matches (no CAPTCHA on the
        current WebView).
        """
        if not _maestro_cli_available():
            # Surfaced via the tool boundary as `no_maestro_cli`; the
            # driver layer raises so visual_challenge.py can translate.
            raise RuntimeError(
                "Maestro CLI is not on PATH. "
                "Install: brew install maestro"
            )

        candidates: list[tuple[str, dict[str, Any]]] = []
        if selector_override and self._fingerprints:
            candidates.append((selector_override, self._fingerprints[0]))
        else:
            for fp in self._fingerprints:
                for sel in fp.get("iframe_selectors", []):
                    candidates.append((sel, fp))

        for iframe_sel, fp in candidates:
            result = self._run_inspect_probe(iframe_sel, fp)
            if result is None:
                # Maestro subprocess errored — skip to next candidate.
                # PR 5 will distinguish "no device" from "iframe missing".
                continue
            if not result.get("fingerprint_id"):
                # Probe ran cleanly but iframe wasn't on screen.
                continue
            return result

        return None

    def execute_solve(
        self,
        record: Any,
        selected_tile_indices: list[int],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        """Click tiles + Verify + read token via one solve mega-YAML.

        Records `attempts_used` / `attempts_remaining` like the
        Playwright path. Returns the same shape as the v0.7
        `_execute_solve()` function — `{status, challenge_id,
        attempts_remaining, token, hint, ...}` — so the caller in
        visual_challenge.py can treat both drivers uniformly.
        """
        if not _maestro_cli_available():
            raise RuntimeError(
                "Maestro CLI is not on PATH. "
                "Install: brew install maestro"
            )

        record.attempts_used += 1
        record.attempts_remaining = max(0, 3 - record.attempts_used)

        fp = record.fingerprint_config or {}
        response_token_selector = fp.get(
            "response_token_selector", 'textarea[name="g-recaptcha-response"]'
        )

        # Tile tap targets — center of each selected tile in device
        # logical pixels. The record's `tiles` array carries the
        # absolute coords (origin = device top-left) captured by the
        # inspect probe; assumes the WebView fills the visible area.
        # PR 6+ refines this with explicit WebView offset for
        # non-full-screen WebViews.
        tile_taps: list[tuple[int, int]] = []
        for idx in selected_tile_indices:
            if idx < 0 or idx >= len(record.tiles):
                return {
                    "status": "error",
                    "challenge_id": record.challenge_id,
                    "attempts_remaining": record.attempts_remaining,
                    "token": None,
                    "hint": f"tile index {idx} out of range (0..{len(record.tiles) - 1})",
                }
            tile = record.tiles[idx]
            cx = int(tile["viewport_x"] + tile["w"] / 2)
            cy = int(tile["viewport_y"] + tile["h"] / 2)
            tile_taps.append((cx, cy))

        verify_xy = getattr(record, "_maestro_verify_xy", None) or (0, 0)
        if verify_xy == (0, 0):
            return {
                "status": "failed",
                "challenge_id": record.challenge_id,
                "attempts_remaining": record.attempts_remaining,
                "token": None,
                "hint": (
                    "Verify button coords not captured during inspect — the "
                    "vendor-specific selector found no match. Re-inspect or "
                    "check that the verify_button_selector in the fingerprint "
                    "table still matches the rendered DOM."
                ),
            }

        wd = self._workdir_path()
        read_token_path = wd / "read_token.js"
        read_token_path.write_text(
            _READ_TOKEN_JS_TEMPLATE.format(
                response_token_selector=response_token_selector.replace("'", "\\'"),
            ),
            encoding="utf-8",
        )

        screenshot_name = f"solve_{os.getpid()}_{record.attempts_used}"
        yaml_body = _build_solve_yaml(
            app_id=self._app_id,
            tile_taps=tile_taps,
            verify_xy=verify_xy,
            read_token_js_path=str(read_token_path),
            screenshot_name=screenshot_name,
        )
        yaml_path = wd / "solve.yaml"
        yaml_path.write_text(yaml_body, encoding="utf-8")

        try:
            proc = self._maestro_test(yaml_path)
        except subprocess.TimeoutExpired:
            return {
                "status": "error",
                "challenge_id": record.challenge_id,
                "attempts_remaining": record.attempts_remaining,
                "token": None,
                "hint": (
                    f"Maestro solve subprocess exceeded {self._timeout_s}s. "
                    "Increase QA_TIMEOUT_SECONDS or check device responsiveness."
                ),
            }
        except OSError as e:
            return {
                "status": "error",
                "challenge_id": record.challenge_id,
                "attempts_remaining": record.attempts_remaining,
                "token": None,
                "hint": f"Maestro subprocess failed: {type(e).__name__}: {e}",
            }

        if proc.returncode != 0:
            return {
                "status": "failed",
                "challenge_id": record.challenge_id,
                "attempts_remaining": record.attempts_remaining,
                "token": None,
                "hint": (
                    f"Maestro solve flow exit_code={proc.returncode}. "
                    f"stderr_tail: {(proc.stderr or '')[-400:]}"
                ),
            }

        fields = _parse_runscript_output(proc.stdout or "")
        token = fields.get("token", "") or ""

        if token:
            return {
                "status": "passed",
                "challenge_id": record.challenge_id,
                "attempts_remaining": record.attempts_remaining,
                "token": token,
                "hint": (
                    f"Tiles {selected_tile_indices} clicked via Maestro. "
                    "CAPTCHA verified. Resume your test."
                ),
            }

        # No token in textarea — solve failed. Caller can retry the
        # whole inspect/judge/solve cycle, capped by attempts_remaining.
        return {
            "status": "failed",
            "challenge_id": record.challenge_id,
            "attempts_remaining": record.attempts_remaining,
            "token": None,
            "hint": (
                "Verify tap succeeded but the response textarea is still "
                "empty. The vendor may have rejected the selection (try "
                "another round) or the response_token_selector is wrong "
                "for this vendor variant."
            ),
        }

    # ---- Internals ------------------------------------------------------

    def _workdir_path(self) -> Path:
        if self._workdir is None:
            self._workdir = Path(tempfile.mkdtemp(prefix="mk-qa-maestro-"))
        return self._workdir

    def _run_inspect_probe(
        self, iframe_selector_css: str, fingerprint: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Generate + run one inspect mega-YAML; parse the result."""
        wd = self._workdir_path()
        probe_path = wd / "inspect_probe.js"
        probe_path.write_text(
            _INSPECT_PROBE_JS_TEMPLATE.format(
                iframe_selector_css=iframe_selector_css.replace("'", "\\'"),
                fingerprint_id=fingerprint["id"],
                challenge_text_selector=fingerprint["challenge_text_selector"].replace("'", "\\'"),
                tile_cell_selector=fingerprint["tile_cell_selector"].replace("'", "\\'"),
                verify_button_selector=fingerprint["verify_button_selector"].replace("'", "\\'"),
            ),
            encoding="utf-8",
        )

        screenshot_name = f"inspect_{os.getpid()}"
        yaml_body = _build_inspect_yaml(
            app_id=self._app_id,
            probe_js_path=str(probe_path),
            screenshot_name=screenshot_name,
        )
        yaml_path = wd / "inspect.yaml"
        yaml_path.write_text(yaml_body, encoding="utf-8")

        try:
            proc = self._maestro_test(yaml_path)
        except subprocess.TimeoutExpired:
            return None
        except OSError:
            return None

        if proc.returncode != 0:
            return None

        fields = _parse_runscript_output(proc.stdout or "")
        if not fields.get("fingerprint_id"):
            return None

        cells = _parse_cells_json(fields.get("cells_json", "[]"))
        tile_count = int(fields.get("tile_count", "0") or "0")
        if tile_count == 0 or not cells:
            return None
        grid_layout = "4x4" if tile_count == 16 else "3x3"

        # Cache DPR for execute_solve coordinate translation (PR 5).
        try:
            vw = float(fields.get("viewport_w", "0"))
            vh = float(fields.get("viewport_h", "0"))
            if vw and vh:
                # Until we read window.devicePixelRatio explicitly,
                # store viewport size as DPR proxy.
                self._dpr_cache[self._device_id] = vw / vh if vh else 1.0
        except ValueError:
            pass

        # Read the screenshot file Maestro just wrote and inline it as
        # base64. Maestro writes to CWD by default.
        screenshot_b64 = _read_screenshot_b64(Path.cwd() / f"{screenshot_name}.png")

        tiles = [
            {
                "index": i,
                "viewport_x": int(cell["x"]),
                "viewport_y": int(cell["y"]),
                "w": int(cell["w"]),
                "h": int(cell["h"]),
            }
            for i, cell in enumerate(cells[:tile_count])
        ]

        # v0.8.0 PR 5: the probe also captures the verify button center
        # so execute_solve can tap there without re-probing. 0,0 means
        # the button wasn't found this round; the record carries this
        # forward to solve which surfaces a clear failure.
        try:
            verify_x = int(float(fields.get("verify_x", "0") or "0"))
            verify_y = int(float(fields.get("verify_y", "0") or "0"))
        except ValueError:
            verify_x, verify_y = 0, 0

        return {
            "screenshot_base64": screenshot_b64,
            "challenge_text": fields.get("challenge_text", "") or "(challenge text unavailable)",
            "grid_layout": grid_layout,
            "tile_count": tile_count,
            "tiles": tiles,
            "fingerprint": f"{fingerprint['id']}-{grid_layout}",
            "fingerprint_id": fingerprint["id"],
            "fingerprint_config": fingerprint,
            # Opaque handle — MaestroDriver uses the iframe selector
            # string as a stand-in. PlaywrightDriver puts a Playwright
            # frame_locator here. Caller treats this as opaque.
            "frame_locator": iframe_selector_css,
            "_coord_method": "maestro_runscript",
            # v0.8.0 PR 5 — maestro-specific: tap target for Verify.
            # PlaywrightDriver doesn't set this (it taps via CSS
            # selector in the frame). The visual_challenge layer
            # passes this through to the record so execute_solve can
            # consume it.
            "_maestro_verify_xy": (verify_x, verify_y),
        }

    def _maestro_test(self, yaml_path: Path) -> subprocess.CompletedProcess:
        """Wrap `subprocess.run(['maestro', 'test', yaml_path])`. Isolated
        so unit tests can monkeypatch it without spawning a real Maestro."""
        cmd = ["maestro"]
        if self._device_id:
            cmd.extend(["--udid", self._device_id])
        cmd.extend(["test", str(yaml_path)])
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=self._timeout_s
        )


def _parse_cells_json(raw: str) -> list[dict[str, Any]]:
    """Defensive JSON parse — returns [] on any parse failure or
    non-list result. Maestro echoes the assignment with embedded quotes
    sometimes; tolerate both bare-array and quoted forms."""
    raw = (raw or "").strip()
    if raw.startswith('"') and raw.endswith('"'):
        # Strip outer quotes Maestro may have wrapped around the value.
        raw = raw[1:-1].replace('\\"', '"')
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    return [d for d in data if isinstance(d, dict)]


def _read_screenshot_b64(png_path: Path) -> str:
    """Read a PNG file and return data:URL-prefixed base64. Empty string
    when the file is missing — caller already handles that case."""
    import base64
    try:
        data = png_path.read_bytes()
    except OSError:
        return ""
    if not data:
        return ""
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")
