"""AI Visual Challenge Solver — Tier 3 of the CAPTCHA strategy.

Two atomic tools: inspect (screenshot + tile metadata) + solve (accept
AI tile selection + execute clicks). The AI client (Claude / Cursor /
Gemini, multimodal) is the actual solver; this module is the eyes
and hands.

Privacy: NO screenshot retention beyond the active inspect->solve
cycle. Telemetry logs boolean outcome only — never screenshots,
challenge text, or tile selection.

Consent: gated by QA_VISUAL_CHALLENGE_CONSENT env var (default false).
Hard-stops on known third-party login domains regardless of consent.

Scope:
  - v0.7.0 — reCAPTCHA v2 image-grid
  - v0.7.1 — hCaptcha image-select (added via fingerprint table)
reCAPTCHA v3 / Cloudflare Turnstile — out of scope permanently
(no visible challenge to inspect).
"""
from __future__ import annotations

import base64
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse


# ---- Hard-stops -----------------------------------------------------------
# Domains where solving a CAPTCHA is never legitimate QA — login portals
# for third-party identity providers. Refused regardless of consent flag.
# Match is by suffix on `host` (so `accounts.google.com.evil` does NOT
# accidentally match `accounts.google.com`).
#
# v0.7.1 additions: discord.com, epicgames.com, mailbox.org — three
# hCaptcha-protected domains commonly abused for credential stuffing.
_FORBIDDEN_DOMAINS: frozenset[str] = frozenset({
    "accounts.google.com",
    "login.microsoftonline.com",
    "id.apple.com",
    "appleid.apple.com",
    "facebook.com",
    "www.facebook.com",
    "login.live.com",
    "login.yahoo.com",
    "twitter.com/login",
    "x.com/login",
    # v0.7.1 — hCaptcha-protected high-abuse targets
    "discord.com",
    "epicgames.com",
    "mailbox.org",
})


# ---- Disclaimer text ------------------------------------------------------

DISCLAIMER_TEXT = (
    "AI Visual Challenge Solver requires explicit consent before use.\n"
    "\n"
    "Set QA_VISUAL_CHALLENGE_CONSENT=true in your environment to enable.\n"
    "\n"
    "ACCEPTABLE USE\n"
    "This tool is intended for QA testing on:\n"
    "- Sites you own\n"
    "- Client sites where you have explicit written authorization\n"
    "- Test environments where Tier 1 bypass (reCAPTCHA test keys, feature\n"
    "  flags, IP allowlist) is unavailable\n"
    "\n"
    "DO NOT USE THIS TOOL ON:\n"
    "- Third-party sites you do not own\n"
    "- Production sites without explicit authorization\n"
    "- Sites where automated access violates TOS or local law\n"
    "\n"
    "Solving CAPTCHAs without authorization may violate the Computer\n"
    "Fraud and Abuse Act (US), GDPR (EU), or equivalent jurisdictions.\n"
    "The user is solely responsible for legal compliance.\n"
    "\n"
    "To proceed: set QA_VISUAL_CHALLENGE_CONSENT=true and, recommended,\n"
    "QA_VISUAL_CHALLENGE_AUTHORIZED_DOMAINS=<comma-separated allowlist>."
)


# ---- Dynamic-replace mode markers ----------------------------------------
# v0.7.4: reCAPTCHA + hCaptcha both have a "dynamic-replace" mode where
# clicking a matching tile replaces it with a new image, and the user
# must keep selecting until no matches remain. The prompt's language is
# the signal: "Click verify once there are none left" (en),
# "確定沒有遺漏" (zh-Hant), etc. Static mode prompts have none of these
# phrases — the user clicks all matches once, then Verify.
#
# Detection is a substring match against the lowercased challenge_text.
# Adding a new language? Append the lowercased marker phrase here.
_DYNAMIC_MODE_MARKERS: tuple[str, ...] = (
    "none left",            # English: "Click verify once there are none left"
    "click verify once",    # English variant
    "if there are none",    # English: "If there are none, click skip"
    "確定沒有遺漏",          # Traditional Chinese
    "確認沒有遺漏",          # Traditional Chinese variant
    "请确认",                # Simplified Chinese
    "もう一度",              # Japanese: "もう一度残っているか確認"
    "残っていない",           # Japanese: "残っていない場合"
)


def _is_dynamic_mode(challenge_text: str) -> bool:
    """True iff the prompt indicates dynamic-replace mode (multi-round).

    Conservative default: returns False when challenge_text is missing
    or the marker phrases don't appear. False means solve will click
    Verify immediately after the tile clicks (legacy static flow).
    """
    if not challenge_text:
        return False
    lower = challenge_text.lower()
    return any(marker.lower() in lower for marker in _DYNAMIC_MODE_MARKERS)


# Hard cap on how many continue-rounds solve will accept before forcing
# Verify. Even a real captcha rarely needs more than 3-4 rounds; the cap
# is a safety net against AI clients getting stuck in a click loop.
_MAX_DYNAMIC_ROUNDS = 5


# ---- Vendor fingerprint table --------------------------------------------
# v0.7.1 introduces a vendor-neutral fingerprint table. Detection iterates
# the list in order; first match wins. Order is load-bearing: reCAPTCHA
# precedes hCaptcha so v0.7.0 behavior is preserved when both iframes are
# present (ratified decision #1 in docs/prd-v0.7.1-hcaptcha.md §11).
_FINGERPRINTS: list[dict[str, Any]] = [
    {
        "id": "recaptcha-v2-image",
        "iframe_selectors": [
            'iframe[title*="recaptcha challenge"]',
            'iframe[src*="recaptcha/api2/bframe"]',
        ],
        # v0.7.3: chain selectors. Real Google reCAPTCHA uses
        # `.rc-imageselect-desc-no-canonical` for dynamic-replace mode
        # ("Click verify once there are none left.") and `.rc-imageselect-desc`
        # for static mode. Real Google reCAPTCHA's table also has a
        # grid-size suffix (`rc-imageselect-table-33`, `-44`); the mock
        # fixture in tests/ uses the unsuffixed legacy class.
        "challenge_text_selector": (
            ".rc-imageselect-desc-no-canonical, .rc-imageselect-desc"
        ),
        "tile_table_selector": (
            'table[class*="rc-imageselect-table"], .rc-imageselect-target,'
            ' .rc-imageselect-table'
        ),
        # v0.7.3: real reCAPTCHA tiles are `<div class="rc-image-tile-wrapper">`
        # with proper 120×120 dimensions. The legacy `<td>` selector matched
        # nothing in production but kept the mock-fixture tests green.
        "tile_cell_selector": ".rc-image-tile-wrapper, .rc-imageselect-table td",
        "verify_button_selector": "#recaptcha-verify-button",
        "response_token_selector": 'textarea[name="g-recaptcha-response"]',
    },
    {
        "id": "hcaptcha-image",
        "iframe_selectors": [
            'iframe[src*="hcaptcha.com"]',
            'iframe[title*="hCaptcha"]',
            'iframe[title*="Main content of the hCaptcha"]',
        ],
        "challenge_text_selector": ".prompt-text",
        "tile_table_selector": ".task-grid",
        "tile_cell_selector": ".task-grid .task, .task",
        "verify_button_selector": ".button-submit",
        "response_token_selector": 'textarea[name="h-captcha-response"]',
    },
]


# ---- Cache record ---------------------------------------------------------

@dataclass
class _ChallengeRecord:
    """In-memory handle to a live visual-challenge.

    Holds the Playwright references we need to translate AI tile
    selections back into actual mouse clicks. Never persisted to disk.
    Vendor-neutral: `fingerprint_id` + `fingerprint_config` reference the
    matched entry in `_FINGERPRINTS` so downstream click + token-read
    paths can look up the right selectors without hardcoding a vendor.
    """
    challenge_id: str
    expires_at: datetime
    grid_layout: str  # "3x3" / "4x4"
    tile_count: int
    tiles: list[dict[str, Any]]
    challenge_text: str
    fingerprint: str
    domain: str
    # v0.7.1: vendor identity + selector config for the matched vendor.
    fingerprint_id: str = "recaptcha-v2-image"
    fingerprint_config: dict[str, Any] = field(default_factory=dict)
    # Playwright handles — typed as Any so this module imports cleanly
    # even when playwright isn't installed (unit tests can build a record
    # with mocks).
    page: Any = None
    frame_locator: Any = None
    attempts_remaining: int = 3
    warning: str | None = None
    # The 0-3 attempts the user has burned so far. reCAPTCHA locks out
    # the verify button after 3 consecutive misses.
    attempts_used: int = 0
    # v0.7.4: count of `solve(continue)` cycles taken in dynamic-replace
    # mode. Capped at _MAX_DYNAMIC_ROUNDS to prevent runaway loops when
    # the AI client keeps finding matches that never resolve. Distinct
    # from attempts_used (which counts Verify-button presses).
    rounds_used: int = 0


# ---- LRU + TTL cache -------------------------------------------------------

_CACHE_MAX = 10
_CACHE_TTL_SECONDS = 300  # 5 minutes

_ACTIVE_CHALLENGES: "OrderedDict[str, _ChallengeRecord]" = OrderedDict()
_CACHE_LOCK = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _evict_expired_locked() -> None:
    """Called while holding _CACHE_LOCK. Drops any entry past its TTL."""
    now = _now()
    expired = [cid for cid, rec in _ACTIVE_CHALLENGES.items() if rec.expires_at <= now]
    for cid in expired:
        _ACTIVE_CHALLENGES.pop(cid, None)


def _store_challenge(rec: _ChallengeRecord) -> None:
    with _CACHE_LOCK:
        _evict_expired_locked()
        # LRU eviction at cap. OrderedDict.move_to_end ensures the
        # most-recently-touched record stays at the right side; we drop
        # from the left (oldest).
        while len(_ACTIVE_CHALLENGES) >= _CACHE_MAX:
            _ACTIVE_CHALLENGES.popitem(last=False)
        _ACTIVE_CHALLENGES[rec.challenge_id] = rec


def _fetch_challenge(challenge_id: str) -> _ChallengeRecord | None:
    with _CACHE_LOCK:
        _evict_expired_locked()
        rec = _ACTIVE_CHALLENGES.get(challenge_id)
        if rec is None:
            return None
        # touch — LRU recency
        _ACTIVE_CHALLENGES.move_to_end(challenge_id)
        return rec


def _drop_challenge(challenge_id: str) -> None:
    with _CACHE_LOCK:
        _ACTIVE_CHALLENGES.pop(challenge_id, None)


def _reset_cache_for_tests() -> None:
    """Test hook — wipe the LRU between unit tests."""
    with _CACHE_LOCK:
        _ACTIVE_CHALLENGES.clear()


# ---- Consent / domain gating ---------------------------------------------

def _consent_required_response() -> dict[str, Any]:
    return {
        "error": "consent_required",
        "retryable": False,
        "hint": DISCLAIMER_TEXT,
        "consent_env": "QA_VISUAL_CHALLENGE_CONSENT",
    }


def _domain_of(url: str) -> str:
    """Lowercase hostname (no port, no path). Empty string if unparseable."""
    if not url:
        return ""
    try:
        parsed = urlparse(url if "://" in url else f"http://{url}")
    except ValueError:
        return ""
    return (parsed.hostname or "").lower()


def _is_forbidden_domain(host: str) -> bool:
    """True when `host` matches a hard-stop domain by suffix."""
    if not host:
        return False
    host = host.lower()
    for forbidden in _FORBIDDEN_DOMAINS:
        # Defensive: a few entries above carry a path component for
        # readability (e.g. twitter.com/login). Strip them down to host
        # before suffix-matching since `host` is hostname-only.
        f_host = forbidden.split("/", 1)[0]
        if host == f_host or host.endswith("." + f_host):
            return True
    return False


def _domain_allowed(host: str, allowlist: frozenset[str] | None) -> tuple[bool, str | None]:
    """Return (allowed, warning).

    - allowlist None → warn-only mode (allowed=True, warning set).
    - allowlist set → strict: must match suffix.
    """
    if not host:
        # No URL to check (e.g. unit tests). Treat as warn-only.
        return True, "page URL unknown — domain allowlist check skipped"
    if allowlist is None:
        return True, (
            "QA_VISUAL_CHALLENGE_AUTHORIZED_DOMAINS is unset — operating in "
            "warn-only mode. Set it to your project's allowlist before using "
            "this tool in CI or shared environments."
        )
    for entry in allowlist:
        entry = entry.lower()
        if host == entry or host.endswith("." + entry):
            return True, None
    return False, None


def _config_snapshot() -> dict[str, Any]:
    """Read the relevant config values fresh on each call.

    Importing inside the function (rather than at module top) means unit
    tests can monkeypatch env vars + reload `config` between cases and
    the gate logic picks up the new values, instead of binding the
    booleans at import time.
    """
    from .. import config

    return {
        "consent": bool(config.QA_VISUAL_CHALLENGE_CONSENT),
        "timeout": int(config.QA_VISUAL_CHALLENGE_TIMEOUT),
        "allowlist": config.QA_VISUAL_CHALLENGE_AUTHORIZED_DOMAINS,
    }


def _telemetry_outcome(passed: bool | None) -> None:
    """Boolean-only telemetry — never log screenshots / challenge text /
    tile selection. Best-effort; never break the tool call on a logging
    failure."""
    try:
        from . import telemetry
        from ..config import TELEMETRY_DIR
        from pathlib import Path
        import json as _json

        TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)
        path = Path(TELEMETRY_DIR) / "visual-challenge.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(_json.dumps({
                "ts": datetime.now().isoformat(timespec="seconds"),
                "passed": passed,
            }) + "\n")
    except Exception:
        pass


# ---- Public tool entry points --------------------------------------------

def inspect_visual_challenge_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    """Detect a visual CAPTCHA iframe, screenshot, return tile metadata.

    Supports reCAPTCHA v2 (since v0.7.0) and hCaptcha (since v0.7.1).
    Vendor selection is automatic — the first fingerprint in
    `_FINGERPRINTS` whose iframe selectors match wins. reCAPTCHA is
    listed first so existing v0.7.0 behavior is preserved when both
    iframes are present on the page.

    Args (all optional):
      page_id: str | None — reserved for future multi-page sessions;
        ignored in v0.7.x (single active page only).
      selector: str | None — override auto-detect.
      _page: Playwright page object (test hook).

    Returns the structured payload documented in PRD §8. Error shapes:
      - {"error": "consent_required", ...}
      - {"error": "unauthorized_domain", ...}
      - {"error": "forbidden_domain", ...}
      - {"error": "no_challenge_present", ...}
      - {"error": "no_active_page", ...}
    """
    cfg = _config_snapshot()
    if not cfg["consent"]:
        return _consent_required_response()

    arguments = arguments or {}
    page = arguments.get("_page")
    selector = arguments.get("selector")

    if page is None:
        # v0.7.0 does not yet broker its own Playwright session; the
        # caller must hand in a `page` object via the runner harness.
        # Surface a structured error rather than crashing.
        return {
            "error": "no_active_page",
            "retryable": False,
            "hint": (
                "inspect_visual_challenge needs an active Playwright page. "
                "Drive a page via run_tests (pytest-playwright) or pass one "
                "into the tool via the _page test hook."
            ),
        }

    # Domain gates --------------------------------------------------------
    page_url = ""
    try:
        page_url = getattr(page, "url", "") or ""
    except Exception:
        page_url = ""
    host = _domain_of(page_url)

    if _is_forbidden_domain(host):
        return {
            "error": "forbidden_domain",
            "retryable": False,
            "hint": (
                f"Domain '{host}' is in the hard-stop list "
                "(third-party identity provider). Refused regardless of "
                "consent. See docs/prd-v0.7-visual-challenge.md §15 R2."
            ),
        }

    allowed, warning = _domain_allowed(host, cfg["allowlist"])
    if not allowed:
        return {
            "error": "unauthorized_domain",
            "retryable": False,
            "hint": (
                f"Domain '{host}' is not in "
                "QA_VISUAL_CHALLENGE_AUTHORIZED_DOMAINS. Update the "
                "allowlist or run this tool against a permitted host."
            ),
        }

    # Detect + screenshot the challenge -----------------------------------
    try:
        detection = _detect_visual_challenge(page, selector_override=selector)
    except Exception as e:
        _telemetry_outcome(None)
        return {
            "error": "detection_failed",
            "retryable": True,
            "hint": f"{type(e).__name__}: {e}",
        }

    if detection is None:
        return {
            "error": "no_challenge_present",
            "retryable": False,
            "hint": "Page may not require CAPTCHA, or it has already been solved",
        }

    challenge_id = uuid.uuid4().hex[:12]
    expires_at = _now() + timedelta(seconds=_CACHE_TTL_SECONDS)

    rec = _ChallengeRecord(
        challenge_id=challenge_id,
        expires_at=expires_at,
        grid_layout=detection["grid_layout"],
        tile_count=detection["tile_count"],
        tiles=detection["tiles"],
        challenge_text=detection["challenge_text"],
        fingerprint=detection["fingerprint"],
        fingerprint_id=detection["fingerprint_id"],
        fingerprint_config=detection["fingerprint_config"],
        domain=host,
        page=page,
        frame_locator=detection.get("frame_locator"),
        warning=warning,
    )
    _store_challenge(rec)

    response: dict[str, Any] = {
        "challenge_id": challenge_id,
        "screenshot_base64": detection["screenshot_base64"],
        "challenge_text": rec.challenge_text,
        "grid_layout": rec.grid_layout,
        "tile_count": rec.tile_count,
        "tiles": rec.tiles,
        "expires_at": rec.expires_at.isoformat(),
        "fingerprint": rec.fingerprint,
        # v0.7.3 — which DOM-probe path produced the tile coordinates.
        # Useful for debugging silent miss-clicks: `iframe_divide` means
        # the geometry is approximate (header/footer chrome was included
        # in the grid math). `per_cell_bbox` / `table_rect_js` mean the
        # coords came from real DOM measurements.
        "_coord_method": detection.get("_coord_method", "iframe_divide"),
    }
    if warning:
        response["warning"] = warning
    return response


def solve_visual_challenge_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    """Apply AI client's tile selection, execute click chain, submit.

    Args:
      challenge_id: str — from inspect_visual_challenge
      selected_tile_indices: list[int] — which tiles to click
      confirm: bool = False — must be True to actually execute

    See PRD §8 / §10 for the full state machine. The single `token`
    field carries the vendor's response token (reCAPTCHA's
    `g-recaptcha-response` or hCaptcha's `h-captcha-response`); the
    `fingerprint` field on the inspect response tells the AI client
    which vendor is active (ratified decision #5 in
    docs/prd-v0.7.1-hcaptcha.md §11).
    """
    cfg = _config_snapshot()
    if not cfg["consent"]:
        return _consent_required_response()

    arguments = arguments or {}
    challenge_id = arguments.get("challenge_id")
    selected = arguments.get("selected_tile_indices") or []
    confirm = bool(arguments.get("confirm", False))

    if not challenge_id:
        return {
            "status": "error",
            "challenge_id": None,
            "attempts_remaining": 0,
            "token": None,
            "hint": "challenge_id is required (returned by inspect_visual_challenge)",
        }

    if not confirm:
        return {
            "status": "confirm_required",
            "challenge_id": challenge_id,
            "attempts_remaining": 3,
            "token": None,
            "hint": (
                "Pass confirm=true to actually execute the click chain. "
                "This safety latch prevents accidental CAPTCHA submission."
            ),
        }

    rec = _fetch_challenge(challenge_id)
    if rec is None:
        return {
            "status": "challenge_not_found",
            "challenge_id": challenge_id,
            "attempts_remaining": 0,
            "token": None,
            "hint": (
                "Unknown challenge_id (expired, evicted, or never inspected). "
                "Call inspect_visual_challenge to start a fresh cycle."
            ),
        }

    if rec.expires_at <= _now():
        _drop_challenge(challenge_id)
        return {
            "status": "expired",
            "challenge_id": challenge_id,
            "attempts_remaining": 0,
            "token": None,
            "hint": (
                "Challenge TTL (5 minutes) elapsed before solve was called. "
                "Re-inspect to get a fresh challenge_id."
            ),
        }

    # Validate tile indices against the recorded grid.
    bad = [i for i in selected if not isinstance(i, int) or i < 0 or i >= rec.tile_count]
    if bad:
        return {
            "status": "error",
            "challenge_id": challenge_id,
            "attempts_remaining": rec.attempts_remaining,
            "token": None,
            "hint": f"tile indices out of range: {bad} (valid: 0..{rec.tile_count - 1})",
        }

    # Execute the click chain.
    try:
        result = _execute_solve(rec, selected, timeout_seconds=cfg["timeout"])
    except Exception as e:
        _telemetry_outcome(False)
        return {
            "status": "error",
            "challenge_id": challenge_id,
            "attempts_remaining": rec.attempts_remaining,
            "token": None,
            "hint": f"{type(e).__name__}: {e}",
        }

    _telemetry_outcome(result.get("status") == "passed")

    # On terminal state (passed / expired) we drop the cache record so a
    # subsequent solve_visual_challenge against the same id returns
    # `challenge_not_found` rather than re-clicking.
    if result.get("status") in ("passed", "expired"):
        _drop_challenge(challenge_id)

    return result


# ---- Detection ------------------------------------------------------------

def _detect_visual_challenge(
    page: Any, selector_override: str | None = None
) -> dict[str, Any] | None:
    """Locate a challenge iframe + screenshot + extract tile geometry.

    Walks `_FINGERPRINTS` in declared order — reCAPTCHA first, then
    hCaptcha — and returns the first matching vendor's payload. Order
    is load-bearing: when both iframes are present on the same page,
    reCAPTCHA wins (ratified v0.7.1 §11 #1). Returns None when no
    fingerprint matches.

    `selector_override`, when supplied, short-circuits vendor lookup —
    we treat the override as a hand-picked iframe selector and use the
    *first* fingerprint as the configuration source (this preserves
    v0.7.0's escape hatch for callers passing a custom reCAPTCHA
    selector).
    """
    # Build the list of (selector, fingerprint_config) pairs we'll probe
    # in order. Selector override path keeps the v0.7.0 contract — the
    # override is a single selector that resolves against the first
    # fingerprint's config (reCAPTCHA defaults).
    candidates: list[tuple[str, dict[str, Any]]] = []
    if selector_override:
        candidates.append((selector_override, _FINGERPRINTS[0]))
    else:
        for fp in _FINGERPRINTS:
            for sel in fp["iframe_selectors"]:
                candidates.append((sel, fp))

    iframe_element = None
    matched_selector: str | None = None
    matched_fp: dict[str, Any] | None = None
    for sel, fp in candidates:
        if not sel:
            continue
        try:
            locator = page.locator(sel)
            count = locator.count() if hasattr(locator, "count") else 0
        except Exception:
            continue
        if count and count > 0:
            iframe_element = locator.first
            matched_selector = sel
            matched_fp = fp
            break

    if iframe_element is None or matched_fp is None:
        return None

    # Bounding box of the iframe in viewport coordinates.
    try:
        box = iframe_element.bounding_box() or {}
    except Exception:
        box = {}

    iframe_x = float(box.get("x") or 0)
    iframe_y = float(box.get("y") or 0)
    iframe_w = float(box.get("width") or 0)
    iframe_h = float(box.get("height") or 0)

    # Screenshot — Playwright returns PNG bytes when path is unset.
    try:
        png_bytes = iframe_element.screenshot()
    except Exception:
        png_bytes = b""
    screenshot_b64 = (
        "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")
        if png_bytes else ""
    )

    # Reach inside the cross-origin iframe via Playwright's frame_locator.
    # Pull challenge text + cell count via the vendor's selectors.
    challenge_text_selector = matched_fp["challenge_text_selector"]
    tile_table_selector = matched_fp["tile_table_selector"]
    # v0.7.3: `tile_cell_selector` is now a *complete* CSS selector for
    # the tile elements (was: a tag name that got concatenated with the
    # table selector). This lets one fingerprint entry chain multiple
    # candidate selectors (real-DOM + legacy-mock) via the CSS comma
    # operator, which the old concat broke.
    cells_selector = matched_fp["tile_cell_selector"]

    frame_locator = None
    challenge_text = ""
    grid_layout = "3x3"
    tile_count = 9
    try:
        frame_locator = page.frame_locator(matched_selector)
        # Challenge instruction text.
        try:
            desc = frame_locator.locator(challenge_text_selector)
            if desc.count() > 0:
                challenge_text = (desc.first.inner_text() or "").strip()
        except Exception:
            challenge_text = ""

        # Grid layout — count cell elements.
        try:
            cells = frame_locator.locator(cells_selector)
            tile_count = cells.count() or 9
        except Exception:
            tile_count = 9
        grid_layout = "4x4" if tile_count == 16 else "3x3"
    except Exception:
        # Frame locator failed — fall back to a default 3x3 layout
        # derived from the bounding box. Useful for test mocks.
        pass

    cols = 4 if grid_layout == "4x4" else 3
    rows = 4 if grid_layout == "4x4" else 3

    # Prefer real per-cell bounding boxes from the DOM. CAPTCHA iframes
    # have a header (prompt text) + table + footer (Verify button), so
    # the table does NOT fill the iframe edge-to-edge. Naively dividing
    # iframe_h / rows misplaces row 2 of a 3x3 grid by ~80px (lands in
    # the footer / Verify-button band) and causes silent miss-clicks on
    # real reCAPTCHA / hCaptcha — even when the AI client's tile
    # judgment is correct.
    tiles: list[dict[str, Any]] = []
    real_cells_resolved = False
    coord_method = "iframe_divide"

    # Path 1: per-cell Playwright bounding_box. Works against mock fixtures
    # (synthetic <td> with real CSS dimensions) but silently fails against
    # production reCAPTCHA — the real <td> elements have zero intrinsic
    # size because cell content is absolute-positioned inside them, so
    # bounding_box() returns None or {0,0,0,0}. Validated against the
    # Google reCAPTCHA demo in v0.7.2 dogfood: every per-cell probe came
    # back non-real, so this branch effectively skipped against real
    # production challenges. Kept as the first attempt because, when it
    # does work, it gives the most accurate per-tile coords.
    if frame_locator is not None:
        try:
            candidate: list[dict[str, Any]] = []
            for index in range(tile_count):
                bb = cells.nth(index).bounding_box()
                # Tight type check — MagicMock returns MagicMock for
                # .get() and dict indexing, so this gracefully refuses
                # non-real bboxes (lets the mock-based unit tests fall
                # through to the iframe-bbox derivation they assert on).
                if not (
                    isinstance(bb, dict)
                    and isinstance(bb.get("x"), (int, float))
                    and isinstance(bb.get("y"), (int, float))
                    and isinstance(bb.get("width"), (int, float))
                    and isinstance(bb.get("height"), (int, float))
                    and bb["width"] > 0
                    and bb["height"] > 0
                ):
                    candidate = []
                    break
                candidate.append({
                    "index": index,
                    "viewport_x": int(round(bb["x"])),
                    "viewport_y": int(round(bb["y"])),
                    "w": int(round(bb["width"])),
                    "h": int(round(bb["height"])),
                })
            if len(candidate) == tile_count:
                tiles = candidate
                real_cells_resolved = True
                coord_method = "per_cell_bbox"
        except Exception:
            pass

    # Path 2 (v0.7.3): JS-evaluate getBoundingClientRect on the table
    # element. Playwright's bounding_box() applies actionability /
    # visibility checks that reCAPTCHA's container fails (children are
    # absolute-positioned, table itself reports 0×0 to Playwright). Raw
    # getBoundingClientRect bypasses those checks and always returns
    # numbers as long as the element exists in the DOM. The returned
    # rect is iframe-window-relative, so we offset by the iframe's
    # page-relative position to get viewport coords that page.mouse.click
    # can use directly.
    #
    # Dividing the table's rect uniformly into rows × cols is accurate
    # for reCAPTCHA + hCaptcha grids because both vendors render fixed-
    # size square tiles inside the table (no padding between cells).
    if not real_cells_resolved and frame_locator is not None:
        try:
            js_rect = (
                "el => {"
                "  const r = el.getBoundingClientRect();"
                "  return {x: r.left, y: r.top, width: r.width, height: r.height};"
                "}"
            )
            table_rect = frame_locator.locator(tile_table_selector).evaluate(js_rect)
            if (
                isinstance(table_rect, dict)
                and isinstance(table_rect.get("x"), (int, float))
                and isinstance(table_rect.get("y"), (int, float))
                and isinstance(table_rect.get("width"), (int, float))
                and isinstance(table_rect.get("height"), (int, float))
                and table_rect["width"] > 0
                and table_rect["height"] > 0
            ):
                t_x = iframe_x + float(table_rect["x"])
                t_y = iframe_y + float(table_rect["y"])
                t_w = float(table_rect["width"]) / cols if cols else 0
                t_h = float(table_rect["height"]) / rows if rows else 0
                tiles = [
                    {
                        "index": i,
                        "viewport_x": int(round(t_x + (i % cols) * t_w)),
                        "viewport_y": int(round(t_y + (i // cols) * t_h)),
                        "w": int(round(t_w)),
                        "h": int(round(t_h)),
                    }
                    for i in range(tile_count)
                ]
                real_cells_resolved = True
                coord_method = "table_rect_js"
        except Exception:
            pass

    if not real_cells_resolved:
        # Fallback: divide the iframe bbox into a uniform grid. Inaccurate
        # for real CAPTCHAs (header/footer chrome offsets row N), but
        # this path is what existing unit tests assert on, and it's the
        # only sensible answer when frame_locator can't enumerate cells.
        cell_w = iframe_w / cols if cols else 0
        cell_h = iframe_h / rows if rows else 0
        for index in range(tile_count):
            row, col = divmod(index, cols)
            tiles.append({
                "index": index,
                "viewport_x": int(round(iframe_x + col * cell_w)),
                "viewport_y": int(round(iframe_y + row * cell_h)),
                "w": int(round(cell_w)),
                "h": int(round(cell_h)),
            })

    fingerprint_id = matched_fp["id"]
    return {
        "screenshot_base64": screenshot_b64,
        "challenge_text": challenge_text or "(challenge text unavailable)",
        "grid_layout": grid_layout,
        "tile_count": tile_count,
        "tiles": tiles,
        "fingerprint": f"{fingerprint_id}-{grid_layout}",
        "fingerprint_id": fingerprint_id,
        "fingerprint_config": matched_fp,
        "frame_locator": frame_locator,
        "_coord_method": coord_method,
    }


# Back-compat alias — keep the v0.7.0 name as a thin shim so any callers
# still importing `_detect_recaptcha` continue to work.
_detect_recaptcha = _detect_visual_challenge


# ---- Execution ------------------------------------------------------------

def _execute_solve(
    rec: _ChallengeRecord,
    selected_tile_indices: list[int],
    timeout_seconds: int,
) -> dict[str, Any]:
    """Issue the click chain + Verify, then probe for the token.

    Returns the same shape as `solve_visual_challenge_tool` — caller is
    responsible for wrapping in error handling and telemetry. Selectors
    are sourced from `rec.fingerprint_config` so reCAPTCHA + hCaptcha
    share this code path verbatim — only the configured selectors and
    response-token name differ between vendors.
    """
    page = rec.page
    rec.attempts_used += 1
    rec.attempts_remaining = max(0, 3 - rec.attempts_used)

    # Resolve vendor-specific selectors. Fall back to the reCAPTCHA
    # config when the record was built before fingerprint_config existed
    # (defensive — shouldn't trip in practice given inspect populates it).
    fp = rec.fingerprint_config or _FINGERPRINTS[0]
    verify_button_selector = fp.get(
        "verify_button_selector", "#recaptcha-verify-button"
    )
    response_token_selector = fp.get(
        "response_token_selector", 'textarea[name="g-recaptcha-response"]'
    )

    # Click each selected tile by viewport coordinate. Small humanized
    # jitter between clicks so a single dispatcher event doesn't tear the
    # entire grid before the vendor's internal selection state catches up.
    for idx in selected_tile_indices:
        tile = rec.tiles[idx]
        x = tile["viewport_x"] + tile["w"] / 2
        y = tile["viewport_y"] + tile["h"] / 2
        try:
            page.mouse.click(x, y)
        except Exception as e:
            return {
                "status": "error",
                "challenge_id": rec.challenge_id,
                "attempts_remaining": rec.attempts_remaining,
                "token": None,
                "hint": f"mouse.click({x},{y}) failed: {type(e).__name__}: {e}",
            }
        time.sleep(0.1)

    # v0.7.4: dynamic-replace mode. When the prompt indicates the user
    # must keep selecting until no matches remain (reCAPTCHA's "Click
    # verify once there are none left" pattern), do NOT click Verify
    # after this round — clicked tiles get replaced with new images and
    # the AI client needs another inspect/judge cycle. Re-screenshot the
    # iframe, refresh tile coords, and return `status: "continue"` so
    # the client knows to call solve again with the next selection.
    # Empty `selected_tile_indices` is the finalize signal: AI looked at
    # the latest grid and found no more matches → fall through to Verify.
    if (
        selected_tile_indices
        and _is_dynamic_mode(rec.challenge_text)
        and rec.rounds_used < _MAX_DYNAMIC_ROUNDS
    ):
        # Let new images load + selection animations settle.
        try:
            page.wait_for_timeout(1500)
        except Exception:
            time.sleep(1.5)
        # Re-detect from the same page. The iframe is still mounted; we
        # just need a fresh screenshot + updated tile geometry (clicked
        # tiles often shift slightly after the replacement animation).
        try:
            fresh = _detect_visual_challenge(page)
        except Exception:
            fresh = None
        if fresh is not None:
            rec.rounds_used += 1
            rec.tiles = fresh["tiles"]
            rec.challenge_text = fresh.get(
                "challenge_text", rec.challenge_text
            ) or rec.challenge_text
            rec.frame_locator = fresh.get("frame_locator", rec.frame_locator)
            return {
                "status": "continue",
                "challenge_id": rec.challenge_id,
                "attempts_remaining": rec.attempts_remaining,
                "rounds_used": rec.rounds_used,
                "token": None,
                "screenshot_base64": fresh.get("screenshot_base64", ""),
                "challenge_text": rec.challenge_text,
                "tiles": rec.tiles,
                "tile_count": fresh.get("tile_count", len(rec.tiles)),
                "grid_layout": fresh.get("grid_layout", rec.grid_layout),
                "fingerprint": fresh.get("fingerprint", rec.fingerprint),
                "hint": (
                    f"Dynamic-replace round {rec.rounds_used}/"
                    f"{_MAX_DYNAMIC_ROUNDS}. Look at the new screenshot "
                    "and call solve_visual_challenge again with the next "
                    "set of matching tile indices. Pass an empty list to "
                    "finalize (click Verify)."
                ),
            }

    # Click the Verify button inside the iframe.
    verify_clicked = False
    if rec.frame_locator is not None:
        try:
            verify = rec.frame_locator.locator(verify_button_selector)
            if verify.count() > 0:
                verify.first.click()
                verify_clicked = True
        except Exception:
            verify_clicked = False

    if not verify_clicked:
        return {
            "status": "failed",
            "challenge_id": rec.challenge_id,
            "attempts_remaining": rec.attempts_remaining,
            "token": None,
            "hint": "Verify button not found inside CAPTCHA iframe.",
        }

    # Poll the parent page for the vendor's response token. Both
    # vendors write the JWT into a hidden textarea on success; presence
    # of a non-empty value == passed. The selector name is the only
    # difference (reCAPTCHA → g-recaptcha-response,
    # hCaptcha → h-captcha-response).
    escaped = response_token_selector.replace("\\", "\\\\").replace("'", "\\'")
    eval_script = (
        "() => { const t = document.querySelector('" + escaped + "'); "
        "return t ? t.value : ''; }"
    )

    deadline = time.monotonic() + min(timeout_seconds, 60)
    token: str | None = None
    while time.monotonic() < deadline:
        try:
            value = page.evaluate(eval_script)
        except Exception:
            value = ""
        if value:
            token = value
            break
        time.sleep(0.5)

    if token:
        return {
            "status": "passed",
            "challenge_id": rec.challenge_id,
            "attempts_remaining": rec.attempts_remaining,
            "token": token,
            "hint": (
                f"Tiles {selected_tile_indices} clicked. CAPTCHA verified. "
                "Resume your test."
            ),
        }

    return {
        "status": "failed",
        "challenge_id": rec.challenge_id,
        "attempts_remaining": rec.attempts_remaining,
        "token": None,
        "hint": (
            "Verify clicked but no token appeared within the budget. "
            "The vendor may have surfaced a dynamic follow-up challenge — "
            "call inspect_visual_challenge again for a fresh challenge_id."
        ),
    }
