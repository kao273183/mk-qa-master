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


def test_tile_coords_prefer_real_cell_bbox_when_dom_resolves(monkeypatch):
    """Regression for the v0.7.0/v0.7.1 production bug: tile geometry was
    computed by dividing the iframe bbox uniformly, but real CAPTCHA
    iframes carry a header (prompt text) + table + footer (Verify button)
    around the table. The naive division misplaces row 2 of a 3x3 by
    ~80px (lands in the footer / Verify-button band) and causes silent
    miss-clicks on real reCAPTCHA / hCaptcha challenges — even when the
    AI client's tile judgment is correct.

    Fix: when frame_locator can enumerate the cells AND each
    `cells.nth(i).bounding_box()` returns a real numeric dict, use those
    cell-level bboxes directly. Fall back to the old iframe-divided math
    only when the DOM probe can't yield valid numbers (test mocks that
    don't wire up per-cell bbox returns hit the fallback — see
    `test_tile_coordinate_math` above which intentionally exercises that
    path).
    """
    vc = _reload_modules_with_env(monkeypatch, QA_VISUAL_CHALLENGE_CONSENT="true")

    # Build a mock page where the iframe is at (49, 228) with 320×450
    # outer dimensions — BUT the table inside starts at iframe_y + 40
    # (header), each cell is 97×97, with 5px gutters between rows.
    # If the buggy iframe-divided math were used, tile 7 (row 2, col 1)
    # would land at viewport_y = 228 + 2*(450/3) = 528 — well below the
    # actual cell. With the fix, it should land on the *real* row 2
    # which starts at iframe_y + 40 + 2*97 = 462.
    iframe_x, iframe_y, iframe_w, iframe_h = 49.0, 228.0, 320.0, 450.0
    header_h = 40.0
    cell_size = 97.0
    cell_bboxes = []
    for row in range(3):
        for col in range(3):
            cell_bboxes.append({
                "x": iframe_x + 5.0 + col * cell_size,
                "y": iframe_y + header_h + row * cell_size,
                "width": cell_size,
                "height": cell_size,
            })

    page = MagicMock()
    page.url = "https://test-fixture.local/signup"

    iframe_loc = MagicMock()
    iframe_loc.count.return_value = 1
    iframe_first = MagicMock()
    iframe_first.bounding_box.return_value = {
        "x": iframe_x, "y": iframe_y, "width": iframe_w, "height": iframe_h,
    }
    iframe_first.screenshot.return_value = b"\x89PNG\r\nmock"
    iframe_loc.first = iframe_first
    page.locator.return_value = iframe_loc

    # Build cells locator that returns REAL bboxes per cell.
    cells_loc = MagicMock()
    cells_loc.count.return_value = 9
    cell_handles = []
    for bb in cell_bboxes:
        c = MagicMock()
        c.bounding_box.return_value = bb
        cell_handles.append(c)
    cells_loc.nth.side_effect = lambda i: cell_handles[i]

    desc_loc = MagicMock()
    desc_loc.count.return_value = 1
    desc_first = MagicMock()
    desc_first.inner_text.return_value = "Select all images with traffic lights"
    desc_loc.first = desc_first

    verify_loc = MagicMock()
    verify_loc.count.return_value = 1
    verify_loc.first = MagicMock()

    def _route(selector: str):
        sel = (selector or "").lower()
        if "rc-imageselect-desc" in sel:
            return desc_loc
        if "td" in sel or "table" in sel:
            return cells_loc
        if "verify" in sel or "rc-button" in sel:
            return verify_loc
        return MagicMock(count=lambda: 0)

    fl = MagicMock()
    fl.locator.side_effect = _route
    page.frame_locator.return_value = fl

    page.evaluate.return_value = "fake-recaptcha-token-xyz"
    page.mouse = MagicMock()

    out = vc.inspect_visual_challenge_tool({"_page": page})
    assert out["grid_layout"] == "3x3"
    assert out["tile_count"] == 9

    # tile 7 = row 2, col 1 — DOM bbox is (49+5+97, 228+40+194, 97, 97)
    #                      = (151, 462, 97, 97). Center = (199.5, 510.5).
    tile7 = out["tiles"][7]
    assert tile7["viewport_x"] == 151, (
        f"row-2 click should land in the real table, not in the footer; "
        f"got viewport_x={tile7['viewport_x']} (buggy=210)"
    )
    assert tile7["viewport_y"] == 462, (
        f"row-2 click should land in the real table, not in the footer; "
        f"got viewport_y={tile7['viewport_y']} (buggy=528)"
    )
    assert tile7["w"] == 97
    assert tile7["h"] == 97

    # Now exercise solve and check the click landed at the *real* cell
    # center (151+48, 462+48) ≈ (199.5, 510.5) — not the buggy
    # iframe-divided center (262, 603).
    cid = out["challenge_id"]
    solved = vc.solve_visual_challenge_tool({
        "challenge_id": cid,
        "selected_tile_indices": [7],
        "confirm": True,
    })
    assert solved["status"] == "passed"

    clicks = [c.args for c in page.mouse.click.call_args_list]
    assert (199.5, 510.5) in clicks, (
        f"tile-7 click should land at real cell center (199.5, 510.5); "
        f"got {clicks} — falling back to iframe-divided coords means "
        f"row-2 misclicks on real CAPTCHAs"
    )


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


# ---------------------------------------------------------------------------
# v0.7.1 — hCaptcha vendor (added via _FINGERPRINTS table extension)
# ---------------------------------------------------------------------------

def _make_mock_hcaptcha_page(url: str = "https://test-fixture.local/signup", *,
                              iframe_box=(100.0, 200.0, 300.0, 300.0),
                              tile_count: int = 9,
                              challenge_text: str = "Please click each image containing a bicycle"):
    """Build a Playwright-shaped mock page that exposes a fake hCaptcha
    iframe. Mirrors `_make_mock_page` but routes the hCaptcha-specific
    selectors (`.prompt-text` / `.task-grid .task` / `.button-submit` /
    `textarea[name="h-captcha-response"]`).

    The top-level `page.locator` returns count=0 for reCAPTCHA selectors
    and count=1 for the hCaptcha iframe selector — that's what lets
    `_detect_visual_challenge`'s ordered probe land on the hCaptcha
    fingerprint entry rather than reCAPTCHA's."""
    page = MagicMock()
    page.url = url

    iframe_first = MagicMock()
    iframe_first.bounding_box.return_value = {
        "x": iframe_box[0], "y": iframe_box[1],
        "width": iframe_box[2], "height": iframe_box[3],
    }
    iframe_first.screenshot.return_value = b"\x89PNG\r\n\x1a\nmock-hcaptcha"

    hcaptcha_iframe_locator = MagicMock()
    hcaptcha_iframe_locator.count.return_value = 1
    hcaptcha_iframe_locator.first = iframe_first

    empty_locator = MagicMock()
    empty_locator.count.return_value = 0

    def _page_locator(selector: str):
        s = (selector or "").lower()
        # reCAPTCHA selectors must miss — otherwise the priority rule
        # would swallow the hCaptcha mock since reCAPTCHA is probed first.
        if "recaptcha" in s:
            return empty_locator
        if "hcaptcha" in s:
            return hcaptcha_iframe_locator
        return empty_locator

    page.locator.side_effect = _page_locator

    # frame_locator → prompt-text + task cells + submit button
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
        if "prompt-text" in sel:
            return desc_locator
        if "task-grid" in sel or ".task" in sel:
            return cells_locator
        if "button-submit" in sel:
            return verify_locator
        return MagicMock(count=lambda: 0)

    frame_locator.locator.side_effect = _frame_locator_routing
    page.frame_locator.return_value = frame_locator

    # Solve-path evaluate returns the hCaptcha token shape.
    page.evaluate.return_value = "fake-hcaptcha-token-xyz789"
    page.mouse = MagicMock()
    return page


def _make_mock_both_iframes_page(url: str = "https://test-fixture.local/signup"):
    """Page where BOTH reCAPTCHA and hCaptcha iframes match. The
    priority rule says reCAPTCHA wins (ratified §11 #1). The simplest
    encoding: the top-level `page.locator` returns count=1 for any
    selector — so the first selector probed (reCAPTCHA's
    `iframe[title*="recaptcha challenge"]`) matches and detection
    short-circuits before ever asking about hCaptcha."""
    page = _make_mock_page(url=url)
    return page


def test_inspect_detects_hcaptcha_iframe(monkeypatch):
    """hCaptcha selectors match; fingerprint reads 'hcaptcha-image-3x3'."""
    vc = _reload_modules_with_env(monkeypatch, QA_VISUAL_CHALLENGE_CONSENT="true")
    page = _make_mock_hcaptcha_page()
    out = vc.inspect_visual_challenge_tool({"_page": page})

    assert "challenge_id" in out, f"expected detection, got: {out}"
    assert out["fingerprint"] == "hcaptcha-image-3x3"
    assert out["grid_layout"] == "3x3"
    assert out["tile_count"] == 9
    assert "bicycle" in out["challenge_text"]


def test_solve_returns_hcaptcha_token(monkeypatch):
    """h-captcha-response textarea read on Verify success — surfaced
    under the single `token` field (per ratified decision #5)."""
    vc = _reload_modules_with_env(monkeypatch, QA_VISUAL_CHALLENGE_CONSENT="true")
    page = _make_mock_hcaptcha_page()

    inspected = vc.inspect_visual_challenge_tool({"_page": page})
    cid = inspected["challenge_id"]

    solved = vc.solve_visual_challenge_tool({
        "challenge_id": cid,
        "selected_tile_indices": [1, 3, 8],
        "confirm": True,
    })
    assert solved["status"] == "passed"
    assert solved["token"] == "fake-hcaptcha-token-xyz789"

    # The evaluate call must have queried for the hCaptcha response
    # textarea — not the reCAPTCHA one. The selector is embedded in the
    # JavaScript string passed to page.evaluate.
    eval_calls = [c.args[0] for c in page.evaluate.call_args_list if c.args]
    assert any("h-captcha-response" in script for script in eval_calls), (
        f"expected an h-captcha-response query; got scripts: {eval_calls}"
    )
    assert not any("g-recaptcha-response" in script for script in eval_calls), (
        "hCaptcha solve must not poll the reCAPTCHA token selector"
    )


def test_fingerprint_field_reports_vendor(monkeypatch):
    """Both fingerprint strings are vendor-prefixed (ratified §11 #2)."""
    vc = _reload_modules_with_env(monkeypatch, QA_VISUAL_CHALLENGE_CONSENT="true")

    recaptcha_out = vc.inspect_visual_challenge_tool({"_page": _make_mock_page()})
    hcaptcha_out = vc.inspect_visual_challenge_tool({"_page": _make_mock_hcaptcha_page()})

    assert recaptcha_out["fingerprint"] == "recaptcha-v2-image-3x3"
    assert hcaptcha_out["fingerprint"] == "hcaptcha-image-3x3"
    # Both must carry the vendor prefix — never bare 'image-3x3'.
    assert recaptcha_out["fingerprint"].startswith("recaptcha-")
    assert hcaptcha_out["fingerprint"].startswith("hcaptcha-")


def test_recaptcha_takes_priority_when_both_iframes_present(monkeypatch):
    """If both iframes are on the page, _detect_visual_challenge returns
    reCAPTCHA. This preserves v0.7.0 behavior for existing callers
    (ratified §11 #1)."""
    vc = _reload_modules_with_env(monkeypatch, QA_VISUAL_CHALLENGE_CONSENT="true")
    page = _make_mock_both_iframes_page()
    out = vc.inspect_visual_challenge_tool({"_page": page})

    assert "challenge_id" in out
    assert out["fingerprint"].startswith("recaptcha-")
    assert not out["fingerprint"].startswith("hcaptcha-")


def test_hcaptcha_4x4_grid_layout(monkeypatch):
    """Rare but valid: hCaptcha can serve a 4x4 grid (16 cells)."""
    vc = _reload_modules_with_env(monkeypatch, QA_VISUAL_CHALLENGE_CONSENT="true")
    page = _make_mock_hcaptcha_page(
        iframe_box=(80.0, 160.0, 400.0, 400.0),
        tile_count=16,
    )
    out = vc.inspect_visual_challenge_tool({"_page": page})

    assert out["grid_layout"] == "4x4"
    assert out["tile_count"] == 16
    assert out["fingerprint"] == "hcaptcha-image-4x4"


def test_discord_hard_stops_with_hcaptcha(monkeypatch):
    """Even with consent + allowlist, discord.com refuses. v0.7.1
    extension to the hard-stop blacklist (ratified §11 #3)."""
    vc = _reload_modules_with_env(
        monkeypatch,
        QA_VISUAL_CHALLENGE_CONSENT="true",
        QA_VISUAL_CHALLENGE_AUTHORIZED_DOMAINS="discord.com",  # even allowlisted!
    )
    page = _make_mock_hcaptcha_page(url="https://discord.com/register")
    out = vc.inspect_visual_challenge_tool({"_page": page})

    assert out["error"] == "forbidden_domain"
    assert "discord.com" in out["hint"]


# ---------------------------------------------------------------------------
# v0.7.4 — Dynamic-replace mode (multi-round support)
# ---------------------------------------------------------------------------

def test_dynamic_mode_detection_english(monkeypatch):
    """`_is_dynamic_mode` matches the canonical English dynamic-replace
    phrases ('none left', 'click verify once', 'if there are none')."""
    vc = _reload_modules_with_env(monkeypatch, QA_VISUAL_CHALLENGE_CONSENT="true")
    assert vc._is_dynamic_mode("Select all images with cars\nClick verify once there are none left.")
    assert vc._is_dynamic_mode("Select all squares with buses\nIf there are none, click skip")
    # Plain static prompt — must NOT trigger dynamic loop.
    assert not vc._is_dynamic_mode("Select all images with traffic lights")
    # Empty / missing prompt defaults to static (conservative).
    assert not vc._is_dynamic_mode("")
    assert not vc._is_dynamic_mode("(challenge text unavailable)")


def test_dynamic_mode_detection_chinese(monkeypatch):
    """Traditional Chinese marker triggers dynamic mode."""
    vc = _reload_modules_with_env(monkeypatch, QA_VISUAL_CHALLENGE_CONSENT="true")
    assert vc._is_dynamic_mode("選取圖片中含有公車的所有圖片\n確定沒有遺漏後，請按一下 [驗證]")
    # Static zh-Hant prompt — must NOT trigger.
    assert not vc._is_dynamic_mode("選取所有包含公車的方塊")


def test_solve_returns_continue_in_dynamic_mode(monkeypatch):
    """When the prompt is dynamic-replace AND the AI selected at least
    one tile, solve must NOT click Verify — it returns `status: continue`
    with a fresh screenshot so the AI can re-evaluate the new grid.
    Verify-button click count must stay at zero."""
    vc = _reload_modules_with_env(monkeypatch, QA_VISUAL_CHALLENGE_CONSENT="true")
    page = _make_mock_page(
        challenge_text="Select all images with buses\nClick verify once there are none left.",
    )

    inspected = vc.inspect_visual_challenge_tool({"_page": page})
    cid = inspected["challenge_id"]

    out = vc.solve_visual_challenge_tool({
        "challenge_id": cid,
        "selected_tile_indices": [0, 4],
        "confirm": True,
    })

    assert out["status"] == "continue"
    assert out["token"] is None
    assert out["rounds_used"] == 1
    assert "screenshot_base64" in out
    assert "tiles" in out
    # Verify must NOT have been clicked yet (it's a click on the in-frame
    # verify locator — the mock surfaces this via call recording on the
    # frame_locator's evaluate path).
    # Sanity: page.evaluate was never called for the token-poll loop,
    # because we bailed before reaching the Verify chain.
    assert page.evaluate.call_count == 0


def test_solve_empty_selection_finalizes_in_dynamic_mode(monkeypatch):
    """The AI signals 'I see no more matches' by passing an empty
    selection in dynamic mode. Server interprets that as 'click Verify
    and check for token' — same flow as static mode."""
    vc = _reload_modules_with_env(monkeypatch, QA_VISUAL_CHALLENGE_CONSENT="true")
    page = _make_mock_page(
        challenge_text="Select all images with buses\nClick verify once there are none left.",
    )

    inspected = vc.inspect_visual_challenge_tool({"_page": page})
    cid = inspected["challenge_id"]

    out = vc.solve_visual_challenge_tool({
        "challenge_id": cid,
        "selected_tile_indices": [],
        "confirm": True,
    })

    # Empty selection in dynamic mode → straight to Verify + token poll.
    assert out["status"] == "passed"
    assert out["token"] == "fake-recaptcha-token-abc123"


def test_solve_static_mode_unchanged(monkeypatch):
    """A regular 'Select all images with X' prompt must still click
    Verify on the first solve call — no continue loop, no behavior change
    vs v0.7.3."""
    vc = _reload_modules_with_env(monkeypatch, QA_VISUAL_CHALLENGE_CONSENT="true")
    page = _make_mock_page(
        challenge_text="Select all images with traffic lights",
    )

    inspected = vc.inspect_visual_challenge_tool({"_page": page})
    cid = inspected["challenge_id"]

    out = vc.solve_visual_challenge_tool({
        "challenge_id": cid,
        "selected_tile_indices": [0, 4],
        "confirm": True,
    })

    assert out["status"] == "passed"
    assert out["token"] == "fake-recaptcha-token-abc123"
    # `rounds_used` not present on static-mode pass — it's a dynamic-only
    # field.
    assert "rounds_used" not in out


def test_solve_dynamic_mode_rounds_cap(monkeypatch):
    """After _MAX_DYNAMIC_ROUNDS continue cycles, solve must force Verify
    even if the AI keeps selecting matches. Prevents infinite loops on
    pathological challenges."""
    vc = _reload_modules_with_env(monkeypatch, QA_VISUAL_CHALLENGE_CONSENT="true")
    page = _make_mock_page(
        challenge_text="Select all images with buses\nClick verify once there are none left.",
    )

    inspected = vc.inspect_visual_challenge_tool({"_page": page})
    cid = inspected["challenge_id"]

    # Burn through the cap by repeatedly calling solve with a non-empty
    # selection — each round should return "continue" until the cap.
    last = None
    for _ in range(vc._MAX_DYNAMIC_ROUNDS):
        last = vc.solve_visual_challenge_tool({
            "challenge_id": cid,
            "selected_tile_indices": [0],
            "confirm": True,
        })
        assert last["status"] == "continue"

    # Next call must NOT continue — cap reached, force Verify path.
    final = vc.solve_visual_challenge_tool({
        "challenge_id": cid,
        "selected_tile_indices": [0],
        "confirm": True,
    })
    assert final["status"] == "passed"
    assert final["token"] == "fake-recaptcha-token-abc123"


# ---------------------------------------------------------------------------
# v0.8 driver-argument surface (forward-compatible — actual maestro
# implementation lands in v0.8.0; see docs/prd-v0.8-mobile-webview-captcha.md)
# ---------------------------------------------------------------------------

def test_inspect_default_driver_is_playwright(monkeypatch):
    """Omitting `_driver` must preserve v0.7 behavior — playwright path runs."""
    vc = _reload_modules_with_env(monkeypatch, QA_VISUAL_CHALLENGE_CONSENT="true")
    page = _make_mock_page()
    out = vc.inspect_visual_challenge_tool({"_page": page})
    # v0.7 happy path returns a challenge_id; surfacing the new arg must
    # not regress that.
    assert "challenge_id" in out
    assert out.get("error") != "driver_not_implemented"


def test_inspect_explicit_playwright_driver_works(monkeypatch):
    """Explicitly passing `_driver='playwright'` is also accepted."""
    vc = _reload_modules_with_env(monkeypatch, QA_VISUAL_CHALLENGE_CONSENT="true")
    page = _make_mock_page()
    out = vc.inspect_visual_challenge_tool({"_page": page, "_driver": "playwright"})
    assert "challenge_id" in out
    assert out.get("error") != "driver_not_implemented"


def test_inspect_maestro_driver_returns_not_implemented(monkeypatch):
    """`_driver='maestro'` is on the v0.8 roadmap — surfacing the arg now
    keeps API shape stable, but actual execution must refuse cleanly so
    AI clients get a clear 'not yet' signal."""
    vc = _reload_modules_with_env(monkeypatch, QA_VISUAL_CHALLENGE_CONSENT="true")
    page = _make_mock_page()
    out = vc.inspect_visual_challenge_tool({"_page": page, "_driver": "maestro"})
    assert out["error"] == "driver_not_implemented"
    assert "supported_drivers" in out
    assert out["supported_drivers"] == ["playwright"]
    assert "v0.8" in out["hint"].lower() or "maestro" in out["hint"].lower()


def test_inspect_driver_name_is_case_insensitive(monkeypatch):
    """Case shouldn't matter — `_driver='MAESTRO'` still routes to the
    not-implemented response, not silently fall through to playwright."""
    vc = _reload_modules_with_env(monkeypatch, QA_VISUAL_CHALLENGE_CONSENT="true")
    page = _make_mock_page()
    out = vc.inspect_visual_challenge_tool({"_page": page, "_driver": "Maestro"})
    assert out["error"] == "driver_not_implemented"


def test_solve_maestro_driver_returns_not_implemented(monkeypatch):
    """Solve must also refuse `_driver='maestro'` for symmetry — otherwise
    you could inspect with the default and solve with a future driver,
    which doesn't make sense."""
    vc = _reload_modules_with_env(monkeypatch, QA_VISUAL_CHALLENGE_CONSENT="true")
    page = _make_mock_page()
    inspected = vc.inspect_visual_challenge_tool({"_page": page})
    out = vc.solve_visual_challenge_tool({
        "challenge_id": inspected["challenge_id"],
        "selected_tile_indices": [0],
        "confirm": True,
        "_driver": "maestro",
    })
    assert out["error"] == "driver_not_implemented"


def test_solve_default_driver_still_works(monkeypatch):
    """Sanity: omitting `_driver` on solve must preserve v0.7 behavior end-to-end."""
    vc = _reload_modules_with_env(monkeypatch, QA_VISUAL_CHALLENGE_CONSENT="true")
    page = _make_mock_page()
    inspected = vc.inspect_visual_challenge_tool({"_page": page})
    out = vc.solve_visual_challenge_tool({
        "challenge_id": inspected["challenge_id"],
        "selected_tile_indices": [0, 4],
        "confirm": True,
    })
    # v0.7 happy path; the new arg must not regress it.
    assert out["status"] == "passed"
    assert out.get("error") != "driver_not_implemented"
