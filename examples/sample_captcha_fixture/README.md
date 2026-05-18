# Sample CAPTCHA fixture — `examples/sample_captcha_fixture/`

A self-contained reCAPTCHA-shaped fixture shipped with mk-qa-master so
the v0.7 visual challenge tools can be exercised end-to-end without
ever calling Google. Used by:

- The `api-captcha` CI job (`.github/workflows/ci.yml`) — Playwright
  routes every `recaptcha`-pattern URL to `iframe.html`, then drives
  `inspect_visual_challenge` + `solve_visual_challenge` against the
  fake challenge.
- Local development — load `index.html` directly in a browser to see
  the layout.

## Files

- `index.html` — outer page with an iframe shaped exactly like a real
  reCAPTCHA v2 challenge: `title="recaptcha challenge expires in two
  minutes"` plus a path matching `recaptcha/api2/bframe`. Also carries
  the hidden `textarea[name="g-recaptcha-response"]` that reCAPTCHA
  populates on success.
- `iframe.html` — the iframe body. Has `.rc-imageselect-desc` for the
  challenge text, a `.rc-imageselect-table` with nine `<td>` cells, and
  an `#recaptcha-verify-button` — the same DOM hooks the v0.7
  detection logic probes for. `postMessage`s the parent on Verify so
  the response textarea fills.

## Local quick-look

```bash
python -m http.server --directory examples/sample_captcha_fixture 8765
# open http://localhost:8765/index.html
```

You should see a 3x3 blue grid with a "Verify" button. Clicking tiles
toggles a selection outline; clicking Verify populates the parent
page's `textarea[name="g-recaptcha-response"]` with a fixture token.

## CI route-mock pattern

The unit tests in `tests/test_visual_challenge.py` don't hit the
network at all — they construct `MagicMock`-shaped page objects that
mirror the Playwright surface. For end-to-end coverage against a real
browser, the recommended pattern (used by the `api-captcha` workflow)
is:

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()
    context = browser.new_context()

    # Route every reCAPTCHA-shaped URL to the local fixture iframe.
    fixture_dir = Path("examples/sample_captcha_fixture")
    def handle_route(route):
        if "recaptcha" in route.request.url:
            route.fulfill(body=(fixture_dir / "iframe.html").read_text(),
                          content_type="text/html")
        else:
            route.continue_()
    context.route("**/*", handle_route)

    page = context.new_page()
    page.goto(f"file://{fixture_dir.resolve()}/index.html")
    # ...then drive inspect_visual_challenge + solve_visual_challenge.
```

The fixture is intentionally static — no Google scripts, no third
party, no analytics. Reading it doesn't ping anyone.

## Why not the real reCAPTCHA test keys?

Google publishes "always-pass" test site keys
(`6LeIxAcTAAAAAJcZVRqyHh71UMIEGNQ_MXjiZKhI` / matching secret) that
bypass the image challenge entirely — which makes them perfect for
Tier 1 in our CAPTCHA strategy (see `get_qa_context section="CAPTCHA"`)
but useless for exercising the Tier 3 visual-solver code path. The
fixture is the Tier-3-specific testbed.
