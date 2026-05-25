"""Manual e2e dogfood for v0.7 AI Visual Challenge Solver.

Runs the visual_challenge tools directly (not via MCP transport)
against Google's official reCAPTCHA v2 demo at
https://www.google.com/recaptcha/api2/demo

Uses sync_playwright because v0.7 production code assumes sync API.
(async caller hits 'coroutine never awaited' on locator.count() —
that's a known v0.7.x gap, will be addressed in a follow-up release.)

What this validates:
  - iframe detection
  - screenshot capture
  - tile coordinate math
  - click execution + Verify
  - token extraction

What it does NOT validate:
  - MCP transport (page brokering from Claude -> mk-qa-master server)
  - That's a v0.7.2+ gap

Usage:
  QA_VISUAL_CHALLENGE_CONSENT=true \\
  QA_VISUAL_CHALLENGE_AUTHORIZED_DOMAINS=google.com \\
  python scripts/dogfood-recaptcha.py
"""
import base64
import json
import os
import sys
from pathlib import Path

if os.environ.get("QA_VISUAL_CHALLENGE_CONSENT", "").lower() not in ("1", "true", "yes"):
    print("ERROR: set QA_VISUAL_CHALLENGE_CONSENT=true before running.")
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mk_qa_master.tools.visual_challenge import (  # noqa: E402
    inspect_visual_challenge_tool,
    solve_visual_challenge_tool,
)

DEMO_URL = "https://www.google.com/recaptcha/api2/demo"
SCREENSHOT_OUT = Path("/tmp/captcha-dogfood-screenshot.png")


def main() -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=300)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        print(f"[1/6] Navigating to {DEMO_URL} ...")
        page.goto(DEMO_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)

        print("[2/6] Clicking the reCAPTCHA checkbox to trigger the image challenge ...")
        try:
            page.frame_locator(
                'iframe[title="reCAPTCHA"]'
            ).locator("#recaptcha-anchor").click(timeout=10_000)
        except Exception as exc:
            print(f"  - Could not click the anchor: {exc}")
            print("  - The demo page may have changed. Try the script again, or open DevTools to find the right selector.")
            browser.close()
            return

        # Wait for the bframe (the actual image challenge iframe) to appear.
        page.wait_for_timeout(2_500)

        print("[3/6] Calling inspect_visual_challenge_tool ...")
        inspect_result = inspect_visual_challenge_tool({"_page": page})
        printable = {k: v for k, v in inspect_result.items() if k != "screenshot_base64"}
        print(json.dumps(printable, indent=2, default=str))

        if "error" in inspect_result:
            print(f"\nInspect returned an error: {inspect_result['error']}")
            print("Possible causes:")
            print("  - no bframe rendered (Google trusted your session, skipped the puzzle)")
            print("  - a different fingerprint pattern in the demo page")
            print("  - an actual bug in detection logic worth investigating")
            input("\nPress Enter to close the browser ...")
            browser.close()
            return

        # Save screenshot for human inspection.
        if "screenshot_base64" in inspect_result and inspect_result["screenshot_base64"]:
            b64 = inspect_result["screenshot_base64"]
            if "," in b64:
                b64 = b64.split(",", 1)[1]
            SCREENSHOT_OUT.write_bytes(base64.b64decode(b64))
            print(f"\n[4/6] Screenshot saved to: {SCREENSHOT_OUT}")
            print(f"  - Challenge text: {inspect_result.get('challenge_text', '(unknown)')}")
            print(f"  - Grid layout:    {inspect_result.get('grid_layout', '(unknown)')}")
            print(f"  - Tile count:     {inspect_result.get('tile_count', '(unknown)')}")
            print(f"  - Fingerprint:    {inspect_result.get('fingerprint', '(unknown)')}")
            print(f"  - challenge_id:   {inspect_result.get('challenge_id', '(unknown)')}")
        else:
            print("WARNING: no screenshot in result. Aborting.")
            browser.close()
            return

        print(f"\n[5/6] Feed the screenshot to your AI client (Claude Code / Cursor / etc).")
        print("Ask: 'Look at this image. Which tile indices contain the requested object?")
        print("       Reply with a JSON array like [0, 4, 7].'")
        print(f"     Screenshot path: {SCREENSHOT_OUT}")
        print()

        selected_str = input("Paste the AI's tile indices (e.g. 0,4,7): ").strip()
        try:
            selected = [int(x.strip()) for x in selected_str.split(",") if x.strip()]
        except ValueError:
            print("Could not parse. Aborting.")
            browser.close()
            return

        print(f"\n[6/6] Calling solve_visual_challenge_tool with indices {selected} ...")
        solve_result = solve_visual_challenge_tool({
            "challenge_id": inspect_result["challenge_id"],
            "selected_tile_indices": selected,
            "confirm": True,
            "_page": page,
        })
        print(json.dumps(solve_result, indent=2, default=str))

        print("\n--- RESULT ---")
        print(f"  status:             {solve_result.get('status')}")
        print(f"  token populated:    {bool(solve_result.get('token'))}")
        print(f"  attempts_remaining: {solve_result.get('attempts_remaining')}")

        input("\nPress Enter to close the browser ...")
        context.close()
        browser.close()


if __name__ == "__main__":
    main()
