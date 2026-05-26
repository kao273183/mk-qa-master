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
        # back to its legacy detection function. Once PR 3 extracts the
        # detection logic into this class, this import goes away.
        from . import visual_challenge as _vc

        return _vc._detect_visual_challenge(
            self._page, selector_override=selector_override
        )

    def execute_solve(
        self,
        record: Any,
        selected_tile_indices: list[int],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        from . import visual_challenge as _vc

        return _vc._execute_solve(
            record,
            selected_tile_indices,
            timeout_seconds=timeout_seconds,
        )

    # PR 3+ will add finer-grained methods here, e.g.:
    #
    #     def find_iframe(self, selector: str) -> Any | None: ...
    #     def iframe_bbox(self, handle: Any) -> dict | None: ...
    #     def iframe_screenshot_png(self, handle: Any) -> bytes: ...
    #     def click_at(self, x: float, y: float) -> None: ...
    #
    # then refactor _detect_visual_challenge() to drive through them.
