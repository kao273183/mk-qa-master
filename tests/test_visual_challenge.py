"""Unit tests for the AI Visual Challenge Solver (v0.7.0).

These exercise the gate / cache / coordinate-math logic without ever
calling out to a live reCAPTCHA. The Playwright surface is mocked via
plain `unittest.mock.MagicMock` objects shaped like the bits of the
Playwright API we touch (page.url, page.locator, page.frame_locator,
page.mouse.click, page.evaluate).

CI: this file gets its own job in `.github/workflows/ci.yml`
(`api-captcha`) so the consent / domain / coordinate logic is verified
on every push without an external network dependency.
"""
from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Test plumbing
# ---------------------------------------------------------------------------

def _reload_modules_with_env(monkeypatch, **env):
    """Reload config + visual_challenge with the supplied env. Returns the
    fresh visual_challenge module — tests bind to *this* reference, not
    the one imported at file top, so each test sees its own gate values."""
    for k in (
        "QA_VISUAL_CHALLENGE_CONSENT",
        "QA_VISUAL_CHALLENGE_TIMEOUT",
        "QA_VISUAL_CHALLENGE_AUTHORIZED_DOMAINS",
    ):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    import mk_qa_master.config as cfg
    importlib.reload(cfg)

    from mk_qa_master.tools import visual_challenge as vc
    importlib.reload(vc)
    vc._reset_cache_for_tests()
    return vc


def _make_mock_page(url: str = "https://test-fixture.local/signup", *,
                    iframe_box=(100.0, 200.0, 300.0, 300.0),
                    tile_count: int = 9,
                    challenge_text: str = "Select all images with traffic lights"):
    """Build a Playwright-shaped mock page that exposes a fake reCAPTCHA
    iframe at the requested bounding box. Tile geometry derives from
    iframe_box + tile_count (3x3 = 9, 4x4 = 16)."""
    page = MagicMock()
    page.url = url

    # Iframe locator (top-level page.locator)
    iframe_locator = MagicMock()
    iframe_locator.count.return_value = 1
    iframe_first = MagicMock()
    iframe_first.bounding_box.return_value = {
        "x": iframe_box[0], "y": iframe_box[1],
        "width": iframe_box[2], "height": iframe_box[3],
    }
    iframe_first.screenshot.return_value = b"\x89PNG\r\n\x1a\nmock-bytes"
    iframe_locator.first = iframe_first
    page.locator.return_value = iframe_locator

    # frame_locator → desc + cells + verify
    frame_locator = MagicMock()
    desc_locator = MagicMock()
    desc_locator.count.return_value = 1
    desc_first = MagicMock()
    desc_first.inner_text.return_value = challenge_text
    desc_locator.first = desc_first

    cells_locator = MagicMock()
    cells_locator.count.return_value = tile_count

    verify_locator = MagicMock()
    verify_locator.count.return_value = 1
    verify_first = MagicMock()
    verify_locator.first = verify_first

    def _frame_locator_routing(selector: str):
        sel = (selector or "").lower()
        if "rc-imageselect-desc" in sel:
            return desc_locator
        if "rc-imageselect-table td" in sel or "td" in sel:
            return cells_locator
        if "recaptcha-verify-button" in sel or "rc-button-default" in sel:
            return verify_locator
        return MagicMock(count=lambda: 0)

    frame_locator.locator.side_effect = _frame_locator_routing
    page.frame_locator.return_value = frame_locator

    # Page-level evaluate + mouse for the solve path
    page.evaluate.return_value = "fake-recaptcha-token-abc123"
    page.mouse = MagicMock()
    return page


# ---------------------------------------------------------------------------
# Consent + confirm gates
# ---------------------------------------------------------------------------

def test_inspect_requires_consent(monkeypatch):
    """Without QA_VISUAL_CHALLENGE_CONSENT=true, inspect refuses."""
    vc = _reload_modules_with_env(monkeypatch)
    out = vc.inspect_visual_challenge_tool({"_page": _make_mock_page()})
    assert out["error"] == "consent_required"
    assert out["consent_env"] == "QA_VISUAL_CHALLENGE_CONSENT"
    assert "DO NOT USE THIS TOOL ON" in out["hint"]


def test_solve_requires_confirm(monkeypatch):
    """`confirm=False` (or omitted) must return `confirm_required` — even
    when a real challenge is sitting in cache."""
    vc = _reload_modules_with_env(monkeypatch, QA_VISUAL_CHALLENGE_CONSENT="true")
    page = _make_mock_page()

    inspected = vc.inspect_visual_challenge_tool({"_page": page})
    assert "challenge_id" in inspected
    cid = inspected["challenge_id"]

    out = vc.solve_visual_challenge_tool({
        "challenge_id": cid,
        "selected_tile_indices": [0, 4, 7],
        # confirm omitted on purpose
    })
    assert out["status"] == "confirm_required"
    assert out["token"] is None


def test_solve_unknown_challenge_id(monkeypatch):
    """An unknown challenge id (expired, evicted, or made up) must surface
    `challenge_not_found` — never auto-click, never crash."""
    vc = _reload_modules_with_env(monkeypatch, QA_VISUAL_CHALLENGE_CONSENT="true")
    out = vc.solve_visual_challenge_tool({
        "challenge_id": "this-id-was-never-issued",
        "selected_tile_indices": [0],
        "confirm": True,
    })
    assert out["status"] == "challenge_not_found"
    assert out["token"] is None


# ---------------------------------------------------------------------------
# Domain gating — hard-stops + allowlist
# ---------------------------------------------------------------------------

def test_forbidden_domain_hard_stops(monkeypatch):
    """Even with consent + matching allowlist, the hard-stop domain list
    refuses. `accounts.google.com` is the canonical example — no QA-test
    scenario justifies a CAPTCHA solver against a third-party identity
    provider."""
    vc = _reload_modules_with_env(
        monkeypatch,
        QA_VISUAL_CHALLENGE_CONSENT="true",
        QA_VISUAL_CHALLENGE_AUTHORIZED_DOMAINS="accounts.google.com",  # even allowlisted!
    )
    page = _make_mock_page(url="https://accounts.google.com/signin")
    out = vc.inspect_visual_challenge_tool({"_page": page})
    assert out["error"] == "forbidden_domain"
    assert "accounts.google.com" in out["hint"]


def test_authorized_domain_allowlist_block(monkeypatch):
    """When the allowlist is set and the page domain doesn't match,
    inspect returns `unauthorized_domain` without screenshotting."""
    vc = _reload_modules_with_env(
        monkeypatch,
        QA_VISUAL_CHALLENGE_CONSENT="true",
        QA_VISUAL_CHALLENGE_AUTHORIZED_DOMAINS="client-staging.example.com",
    )
    page = _make_mock_page(url="https://random-third-party.com/signup")
    out = vc.inspect_visual_challenge_tool({"_page": page})
    assert out["error"] == "unauthorized_domain"
    assert "random-third-party.com" in out["hint"]


def test_authorized_domain_allowlist_unset_warns(monkeypatch):
    """When the allowlist is UNSET, inspect proceeds but stamps the
    response with a warning string nudging the operator to set one."""
    vc = _reload_modules_with_env(monkeypatch, QA_VISUAL_CHALLENGE_CONSENT="true")
    page = _make_mock_page(url="https://client-staging.example.com/signup")
    out = vc.inspect_visual_challenge_tool({"_page": page})

    assert "challenge_id" in out, f"expected a challenge, got: {out}"
    assert "warning" in out
    assert "QA_VISUAL_CHALLENGE_AUTHORIZED_DOMAINS" in out["warning"]


# ---------------------------------------------------------------------------
# Coordinate math
# ---------------------------------------------------------------------------

def test_tile_coordinate_math(monkeypatch):
    """Given a mock iframe at (100, 200) with a 3x3 grid of 100x100 cells,
    tile 4 (center) should resolve to (250, 350) viewport-relative — i.e.
    iframe.x + col*100 = 100 + 100 = 200 for the cell's left edge, plus
    half-cell offset of 50 → 250. Same math vertically."""
    vc = _reload_modules_with_env(monkeypatch, QA_VISUAL_CHALLENGE_CONSENT="true")

    page = _make_mock_page(
        url="https://test-fixture.local/signup",
        iframe_box=(100.0, 200.0, 300.0, 300.0),  # 3x3 of 100px cells
        tile_count=9,
    )
    out = vc.inspect_visual_challenge_tool({"_page": page})
    assert out["grid_layout"] == "3x3"
    assert out["tile_count"] == 9

    # tile 4 = row 1, col 1 (center). Left edge = 100 + 1*100 = 200.
    # Top edge = 200 + 1*100 = 300. Width/height = 100.
    center = out["tiles"][4]
    assert center["viewport_x"] == 200
    assert center["viewport_y"] == 300
    assert center["w"] == 100
    assert center["h"] == 100

    # Now exercise the click chain — center of tile 4 is (250, 350).
    cid = out["challenge_id"]
    solved = vc.solve_visual_challenge_tool({
        "challenge_id": cid,
        "selected_tile_indices": [4],
        "confirm": True,
    })
    assert solved["status"] == "passed"
    assert solved["token"] == "fake-recaptcha-token-abc123"

    # page.mouse.click was called with the center coordinate.
    called_args = [c.args for c in page.mouse.click.call_args_list]
    assert (250.0, 350.0) in called_args, f"expected center click; got {called_args}"


# ---------------------------------------------------------------------------
# Cache TTL + LRU
# ---------------------------------------------------------------------------

def test_challenge_cache_ttl(monkeypatch):
    """An entry past its expires_at must surface `expired` on solve, not
    auto-click an old challenge."""
    vc = _reload_modules_with_env(monkeypatch, QA_VISUAL_CHALLENGE_CONSENT="true")
    page = _make_mock_page()
    out = vc.inspect_visual_challenge_tool({"_page": page})
    cid = out["challenge_id"]

    # Forcibly rewind the cached record's expiry into the past.
    rec = vc._ACTIVE_CHALLENGES[cid]
    rec.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    # Re-store so the eviction-on-read path sees it
    vc._ACTIVE_CHALLENGES[cid] = rec

    solved = vc.solve_visual_challenge_tool({
        "challenge_id": cid,
        "selected_tile_indices": [0],
        "confirm": True,
    })
    # After TTL, the record is dropped on read — so we see
    # `challenge_not_found` rather than `expired`. Either is acceptable
    # PRD-wise (both signal "re-inspect"); accept both for resilience.
    assert solved["status"] in ("expired", "challenge_not_found")
    assert solved["token"] is None


def test_challenge_cache_lru(monkeypatch):
    """Adding an 11th challenge must evict the oldest (LRU at 10)."""
    vc = _reload_modules_with_env(monkeypatch, QA_VISUAL_CHALLENGE_CONSENT="true")

    ids: list[str] = []
    for i in range(11):
        page = _make_mock_page(
            # Each fixture under the same host stays inside the warn-only
            # path. The host differs per iteration via path to make the
            # mocks distinct objects.
            url=f"https://test-fixture.local/signup-{i}",
        )
        out = vc.inspect_visual_challenge_tool({"_page": page})
        assert "challenge_id" in out, f"iter {i}: {out}"
        ids.append(out["challenge_id"])

    # Cache should be exactly 10 entries.
    assert len(vc._ACTIVE_CHALLENGES) == 10
    # The oldest (id 0) should be gone; the newest (id 10) should be present.
    assert ids[0] not in vc._ACTIVE_CHALLENGES
    assert ids[-1] in vc._ACTIVE_CHALLENGES


# ---------------------------------------------------------------------------
# No-challenge + no-page paths
# ---------------------------------------------------------------------------

def test_no_challenge_present(monkeypatch):
    """A page with zero matching iframes returns `no_challenge_present` —
    not an exception, not a crash."""
    vc = _reload_modules_with_env(monkeypatch, QA_VISUAL_CHALLENGE_CONSENT="true")

    page = MagicMock()
    page.url = "https://test-fixture.local/no-captcha-here"
    no_match = MagicMock()
    no_match.count.return_value = 0
    page.locator.return_value = no_match

    out = vc.inspect_visual_challenge_tool({"_page": page})
    assert out["error"] == "no_challenge_present"


def test_no_active_page(monkeypatch):
    """When the caller doesn't hand in a page, surface a structured
    error rather than dereferencing None."""
    vc = _reload_modules_with_env(monkeypatch, QA_VISUAL_CHALLENGE_CONSENT="true")
    out = vc.inspect_visual_challenge_tool({})
    assert out["error"] == "no_active_page"
