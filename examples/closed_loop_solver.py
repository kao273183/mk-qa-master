"""Closed-loop visual challenge solver — Python script edition.

The MCP server (`mk_qa_master.tools.visual_challenge`) is intentionally
just eyes-and-hands: it screenshots the challenge and clicks tiles it's
told to click. The actual *which tiles match the prompt* judgment lives
in the AI client (Claude / Cursor / Gemini / Codex) when this is driven
through MCP.

This standalone script closes the loop for direct-from-Python use cases
— integration tests, CI smoke runs, headed debugging — by plugging a
multimodal LLM call between `inspect_visual_challenge` and
`solve_visual_challenge`.

Three providers supported:

- claude   — Anthropic Claude (vision)  → needs `pip install anthropic`,
             env `ANTHROPIC_API_KEY`
- gemini   — Google Gemini (vision)     → needs `pip install google-genai`,
             env `GOOGLE_API_KEY` or `GEMINI_API_KEY`
- stub     — Deterministic "pick all tiles" — for local fixture
             validation where the verify-callback always passes.

Run:

    python examples/closed_loop_solver.py \\
        --url http://localhost:8765/index.html \\
        --provider claude

If you don't have an LLM key handy, run with `--provider stub` against
the local fixture (see `examples/sample_captcha_fixture/`) to verify
the inspect → solve pipeline works end-to-end without spending tokens.
"""
from __future__ import annotations

import argparse
import base64
import functools
import http.server
import json
import os
import re
import socketserver
import sys
import threading
import time
from pathlib import Path
from typing import Any

# Make `src/` importable when running from repo root or examples/.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))


# ---------------------------------------------------------------------------
# LLM provider adapters
# ---------------------------------------------------------------------------

_VISION_PROMPT_TEMPLATE = (
    "You are solving a CAPTCHA. The image shows a {grid} grid of tiles "
    "indexed 0 through {last_index} (top-left = 0, row-major). "
    "Challenge prompt: {challenge_text!r}.\n\n"
    "Reply with ONLY a JSON array of integer tile indices that match the "
    "prompt. No prose, no markdown fence — just the array. "
    "Examples: [0, 2, 5]  or  []  (when no tiles match)."
)


def _parse_indices(raw: str, tile_count: int) -> list[int]:
    """Lift a JSON array of ints out of free-form LLM output."""
    match = re.search(r"\[[^\[\]]*\]", raw)
    if not match:
        raise ValueError(f"no JSON array in LLM reply: {raw[:200]!r}")
    indices = json.loads(match.group(0))
    if not isinstance(indices, list):
        raise ValueError(f"expected list, got {type(indices).__name__}")
    out: list[int] = []
    for i in indices:
        if not isinstance(i, int):
            continue
        if 0 <= i < tile_count:
            out.append(i)
    return out


def _judge_with_claude(
    *, screenshot_b64: str, challenge_text: str, grid: str, tile_count: int
) -> list[int]:
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError(
            "anthropic SDK not installed. Run: pip install anthropic"
        ) from e
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    client = anthropic.Anthropic(api_key=api_key)
    prompt = _VISION_PROMPT_TEMPLATE.format(
        grid=grid, last_index=tile_count - 1, challenge_text=challenge_text
    )
    msg = client.messages.create(
        model=os.getenv("CLAUDE_MODEL", "claude-opus-4-7"),
        max_tokens=200,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": screenshot_b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )
    text = "".join(block.text for block in msg.content if hasattr(block, "text"))
    return _parse_indices(text, tile_count)


def _judge_with_gemini(
    *, screenshot_b64: str, challenge_text: str, grid: str, tile_count: int
) -> list[int]:
    try:
        from google import genai
        from google.genai import types
    except ImportError as e:
        raise RuntimeError(
            "google-genai SDK not installed. Run: pip install google-genai"
        ) from e
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY or GEMINI_API_KEY not set")

    client = genai.Client(api_key=api_key)
    prompt = _VISION_PROMPT_TEMPLATE.format(
        grid=grid, last_index=tile_count - 1, challenge_text=challenge_text
    )
    image_bytes = base64.b64decode(screenshot_b64)
    response = client.models.generate_content(
        model=os.getenv("GEMINI_MODEL", "gemini-3.1-flash"),
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
            prompt,
        ],
    )
    return _parse_indices(response.text or "", tile_count)


def _judge_with_stub(
    *, screenshot_b64: str, challenge_text: str, grid: str, tile_count: int
) -> list[int]:
    # For the local fixture which passes regardless of selection.
    return list(range(tile_count))


_PROVIDERS = {
    "claude": _judge_with_claude,
    "gemini": _judge_with_gemini,
    "stub": _judge_with_stub,
}


# ---------------------------------------------------------------------------
# Optional local HTTP server (when --serve-fixture is used)
# ---------------------------------------------------------------------------


def _start_fixture_server(directory: Path) -> tuple[socketserver.TCPServer, int]:
    dir_str = str(directory)

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=dir_str, **kw)

        def log_message(self, *_a, **_kw):
            return

    httpd = socketserver.TCPServer(("127.0.0.1", 0), Quiet)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def run(args: argparse.Namespace) -> int:
    # Consent + auth gates set BEFORE importing the gated module so
    # _config_snapshot() picks them up. This script bypasses the human
    # consent confirmation precisely because it's *the* automated agent
    # — using it on third-party domains is on you, see PRD §6.
    os.environ.setdefault("QA_VISUAL_CHALLENGE_CONSENT", "true")
    if args.authorized_domains:
        os.environ["QA_VISUAL_CHALLENGE_AUTHORIZED_DOMAINS"] = args.authorized_domains

    httpd = None
    target_url = args.url
    if args.serve_fixture:
        fixture_dir = _REPO_ROOT / "examples" / args.serve_fixture
        if not fixture_dir.is_dir():
            print(f"ERROR: fixture dir not found: {fixture_dir}", file=sys.stderr)
            return 2
        httpd, port = _start_fixture_server(fixture_dir)
        target_url = f"http://localhost:{port}/index.html"
        print(f"[fixture] serving {fixture_dir} at {target_url}")

    try:
        from mk_qa_master.tools import visual_challenge as vc
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        print(f"ERROR: missing dependency: {e}", file=sys.stderr)
        return 2

    judge = _PROVIDERS[args.provider]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        ctx = browser.new_context(viewport={"width": 800, "height": 700})
        page = ctx.new_page()
        page.goto(target_url, wait_until="load")
        time.sleep(0.5)

        for attempt in range(1, args.max_attempts + 1):
            print(f"\n--- attempt {attempt}/{args.max_attempts} ---")

            r1 = vc.inspect_visual_challenge_tool({"_page": page})
            if r1.get("error"):
                print(f"inspect failed: {r1}")
                browser.close()
                return 1

            print(
                f"  fingerprint={r1['fingerprint']}  grid={r1['grid_layout']}  "
                f"tiles={r1['tile_count']}  text={r1['challenge_text']!r}"
            )

            try:
                indices = judge(
                    screenshot_b64=r1["screenshot_base64"],
                    challenge_text=r1["challenge_text"],
                    grid=r1["grid_layout"],
                    tile_count=r1["tile_count"],
                )
            except Exception as e:
                print(f"  judge ({args.provider}) failed: {type(e).__name__}: {e}")
                browser.close()
                return 1

            print(f"  AI picked: {indices}")

            r2 = vc.solve_visual_challenge_tool(
                {
                    "challenge_id": r1["challenge_id"],
                    "selected_tile_indices": indices,
                    "confirm": True,
                }
            )
            print(f"  solve status={r2.get('status')}  hint={r2.get('hint', '')[:80]}")

            if r2.get("status") == "passed":
                print(f"\n  ✓ PASSED — token={r2.get('token', '')[:48]}")
                browser.close()
                return 0
            if r2.get("status") in ("expired", "challenge_not_found"):
                print("\n  ✗ unrecoverable")
                browser.close()
                return 1
            # Otherwise loop: vendor may have surfaced a fresh challenge
            time.sleep(0.5)

        print("\n  ✗ exhausted max_attempts")
        browser.close()
        return 1

    if httpd is not None:
        httpd.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--url",
        help="Target URL to drive. Required unless --serve-fixture is set.",
    )
    parser.add_argument(
        "--serve-fixture",
        choices=["sample_captcha_fixture", "sample_hcaptcha_fixture"],
        help="Spin up a local HTTP server for the named fixture and use it as --url.",
    )
    parser.add_argument(
        "--provider",
        choices=list(_PROVIDERS),
        default="stub",
        help="Which LLM to use for tile judgment. 'stub' just picks all tiles "
        "(use with --serve-fixture for offline smoke).",
    )
    parser.add_argument(
        "--authorized-domains",
        help="Comma-separated allowlist passed via QA_VISUAL_CHALLENGE_AUTHORIZED_DOMAINS.",
    )
    parser.add_argument("--headless", action="store_true", help="Run Chromium headless.")
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=3,
        help="reCAPTCHA gives ~3 attempts before locking out — mirror that.",
    )
    args = parser.parse_args()
    if not args.url and not args.serve_fixture:
        parser.error("either --url or --serve-fixture is required")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
