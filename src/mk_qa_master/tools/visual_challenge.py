"""AI Visual Challenge Solver — Tier 3 of the CAPTCHA strategy.

Two atomic tools: inspect (screenshot + tile metadata) + solve (accept
AI tile selection + execute clicks). The AI client (Claude / Cursor /
Gemini, multimodal) is the actual solver; this module is the eyes
and hands.

Privacy: NO screenshot retention beyond the active inspect→solve
cycle. Telemetry logs boolean outcome only — never screenshots,
challenge text, or tile selection.

Consent: gated by QA_VISUAL_CHALLENGE_CONSENT env var (default false).
Hard-stops on known third-party login domains regardless of consent.

Scope: reCAPTCHA v2 image-grid only in v0.7.0. hCaptcha → v0.7.1.
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


# ---- Cache record ---------------------------------------------------------

@dataclass
class _ChallengeRecord:
    """In-memory handle to a live reCAPTCHA challenge.

    Holds the Playwright references we need to translate AI tile
    selections back into actual mouse clicks. Never persisted to disk.
    """
    challenge_id: str
    expires_at: datetime
    grid_layout: str  # "3x3" / "4x4"
    tile_count: int
    tiles: list[dict[str, Any]]
    challenge_text: str
    fingerprint: str
    domain: str
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
    """Detect reCAPTCHA v2 iframe, screenshot, return tile metadata.

    Args (all optional):
      page_id: str | None — reserved for future multi-page sessions;
        ignored in v0.7.0 (single active page only).
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
        detection = _detect_recaptcha(page, selector_override=selector)
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

    See PRD §8 / §10 for the full state machine.
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

# v0.7.0 supports two iframe fingerprints — covers reCAPTCHA v2 in
# English UIs (title attr) and any locale (URL pattern). hCaptcha lands
# in v0.7.1 with a third fingerprint added here.
_RECAPTCHA_TITLE_SELECTOR = 'iframe[title*="recaptcha challenge"]'
_RECAPTCHA_URL_SELECTOR = 'iframe[src*="recaptcha/api2/bframe"]'


def _detect_recaptcha(page: Any, selector_override: str | None = None) -> dict[str, Any] | None:
    """Locate the challenge iframe + screenshot + extract tile geometry.

    Returns None when no challenge is present.
    """
    candidate_selectors = [selector_override] if selector_override else [
        _RECAPTCHA_TITLE_SELECTOR,
        _RECAPTCHA_URL_SELECTOR,
    ]

    iframe_element = None
    matched_selector = None
    for sel in candidate_selectors:
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
            break

    if iframe_element is None:
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
    frame_locator = None
    challenge_text = ""
    grid_layout = "3x3"
    tile_count = 9
    try:
        frame_locator = page.frame_locator(matched_selector)
        # Challenge instruction text.
        try:
            desc = frame_locator.locator(".rc-imageselect-desc, .rc-imageselect-desc-no-canonical")
            if desc.count() > 0:
                challenge_text = (desc.first.inner_text() or "").strip()
        except Exception:
            challenge_text = ""

        # Grid layout — count td cells.
        try:
            cells = frame_locator.locator(".rc-imageselect-table td")
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
    cell_w = iframe_w / cols if cols else 0
    cell_h = iframe_h / rows if rows else 0

    tiles: list[dict[str, Any]] = []
    for index in range(tile_count):
        row, col = divmod(index, cols)
        tiles.append({
            "index": index,
            "viewport_x": int(round(iframe_x + col * cell_w)),
            "viewport_y": int(round(iframe_y + row * cell_h)),
            "w": int(round(cell_w)),
            "h": int(round(cell_h)),
        })

    return {
        "screenshot_base64": screenshot_b64,
        "challenge_text": challenge_text or "(challenge text unavailable)",
        "grid_layout": grid_layout,
        "tile_count": tile_count,
        "tiles": tiles,
        "fingerprint": f"recaptcha-v2-image-{grid_layout}",
        "frame_locator": frame_locator,
    }


# ---- Execution ------------------------------------------------------------

def _execute_solve(
    rec: _ChallengeRecord,
    selected_tile_indices: list[int],
    timeout_seconds: int,
) -> dict[str, Any]:
    """Issue the click chain + Verify, then probe for the token.

    Returns the same shape as `solve_visual_challenge_tool` — caller is
    responsible for wrapping in error handling and telemetry.
    """
    page = rec.page
    rec.attempts_used += 1
    rec.attempts_remaining = max(0, 3 - rec.attempts_used)

    # Click each selected tile by viewport coordinate. Small humanized
    # jitter between clicks so a single dispatcher event doesn't tear the
    # entire grid before reCAPTCHA's internal selection state catches up.
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

    # Click the Verify button inside the iframe.
    verify_clicked = False
    if rec.frame_locator is not None:
        try:
            verify = rec.frame_locator.locator("#recaptcha-verify-button, .rc-button-default")
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

    # Poll the parent page for the g-recaptcha-response token. reCAPTCHA
    # writes the JWT into a hidden textarea on success; presence == passed.
    deadline = time.monotonic() + min(timeout_seconds, 60)
    token: str | None = None
    while time.monotonic() < deadline:
        try:
            value = page.evaluate(
                "() => { const t = document.querySelector("
                "'textarea[name=\"g-recaptcha-response\"]'); "
                "return t ? t.value : ''; }"
            )
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
            "reCAPTCHA may have surfaced a dynamic follow-up challenge — "
            "call inspect_visual_challenge again for a fresh challenge_id."
        ),
    }
