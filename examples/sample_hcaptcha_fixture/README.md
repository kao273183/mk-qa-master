# Sample hCaptcha fixture — `examples/sample_hcaptcha_fixture/`

A self-contained hCaptcha-shaped fixture shipped with mk-qa-master
v0.7.1 so the visual challenge tools can be exercised end-to-end
against the second supported vendor without ever calling
hCaptcha.com. Mirrors the structure of the existing
[`examples/sample_captcha_fixture/`](../sample_captcha_fixture/) (the
reCAPTCHA fixture from v0.7.0) — same two-file layout, same Playwright
route-mock pattern, only the selectors and vendor identity differ.

Used by:

- The `api-hcaptcha` CI job (`.github/workflows/ci.yml`) — Playwright
  routes every `hcaptcha.com`-pattern URL to `iframe.html`, then
  drives `inspect_visual_challenge` + `solve_visual_challenge` against
  the fake challenge.
- Local development — load `index.html` directly in a browser to see
  the layout.

## Files

- `index.html` — outer page with an iframe shaped exactly like a
  live hCaptcha challenge: `title="Main content of the hCaptcha
  challenge"` plus a path matching `hcaptcha.com/captcha/v1/bframe`.
  Also carries the hidden `textarea[name="h-captcha-response"]` that
  hCaptcha populates on success.
- `iframe.html` — the iframe body. Has `.prompt-text` for the
  challenge text, a `.task-grid` with nine `.task` cells, and a
  `.button-submit` — the same DOM hooks the v0.7.1 detection logic
  probes for via the hCaptcha entry in `_FINGERPRINTS`.
  `postMessage`s the parent on Verify so the response textarea fills.

## Local quick-look

```bash
python -m http.server --directory examples/sample_hcaptcha_fixture 8766
# open http://localhost:8766/index.html
```

You should see a 3x3 teal grid with a "Verify" button. Clicking
tiles toggles a selection outline; clicking Verify populates the
parent page's `textarea[name="h-captcha-response"]` with a fixture
token.

## CI route-mock pattern

The unit tests in `tests/test_visual_challenge.py` don't hit the
network at all — they construct `MagicMock`-shaped page objects that
mirror the Playwright surface. For end-to-end coverage against a real
browser, the recommended pattern (used by the `api-hcaptcha`
workflow) is:

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()
    context = browser.new_context()

    # Route every hCaptcha-shaped URL to the local fixture iframe.
    fixture_dir = Path("examples/sample_hcaptcha_fixture")
    def handle_route(route):
        if "hcaptcha" in route.request.url:
            route.fulfill(body=(fixture_dir / "iframe.html").read_text(),
                          content_type="text/html")
        else:
            route.continue_()
    context.route("**/*", handle_route)

    page = context.new_page()
    page.goto(f"file://{fixture_dir.resolve()}/index.html")
    # ...then drive inspect_visual_challenge + solve_visual_challenge.
```

The fixture is intentionally static — no hCaptcha scripts, no third
party, no analytics. Reading it doesn't ping anyone.

## Why not the real hCaptcha test sitekey?

hCaptcha publishes test sitekeys (e.g.
`10000000-ffff-ffff-ffff-000000000001`) that always pass — perfect
for Tier 1 in the CAPTCHA strategy (see `get_qa_context
section="CAPTCHA"`) but useless for exercising the Tier 3
visual-solver code path. The fixture is the Tier-3-specific testbed
for hCaptcha, mirroring how the reCAPTCHA fixture serves that role
for the older vendor.
