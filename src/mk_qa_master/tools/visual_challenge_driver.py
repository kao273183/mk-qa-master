"""Driver layer for the Visual Challenge Solver (v0.8.0 PRD §4).

v0.7.x had Playwright calls hardcoded inline in visual_challenge.py.
v0.8 introduces a driver Protocol so a Maestro-backed implementation
can plug in for mobile WebView solves without rewriting the orchestration
logic in visual_challenge.py.

This module ships in two stages:

  PR 2 (this commit) — Protocol + PlaywrightDriver thin facade that
                       delegates to the existing module-level functions
                       in visual_challenge.py. No behavior change; the
                       Playwright call sites stay where they are. The
                       seam is now in place for PR 3+ to lift the
                       per-call abstractions out of the legacy functions.

  PR 3              — extract per-operation methods (find_iframe /
                       iframe_bbox / cell_bbox / click_at / ...) into
                       PlaywrightDriver and refactor _detect_visual_challenge
                       to consume them. Still no behavior change.

  PR 4              — MaestroDriver implementing the same Protocol in
                       mega-YAML mode (PRD §4). One `maestro test`
                       subprocess per solve round, ~37 s per round vs
                       ~9 minutes for a naive per-op design.

The PRD lives at docs/prd-v0.8-mobile-webview-captcha.md. §11 ratifies
all seven design decisions including mega-YAML mode for MaestroDriver.
"""
from __future__ import annotations

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
