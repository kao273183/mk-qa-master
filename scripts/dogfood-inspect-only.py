"""Inspect-only dogfood — verify v0.7.2 coord fix without needing
human-in-the-loop. Opens browser, triggers challenge, prints inspect
output, saves screenshot, exits.
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

from mk_qa_master.tools.visual_challenge import inspect_visual_challenge_tool  # noqa: E402

DEMO_URL = "https://www.google.com/recaptcha/api2/demo"
SCREENSHOT_OUT = Path("/tmp/captcha-inspect-only.png")


def main() -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        print(f"[1/4] Navigate {DEMO_URL}")
        page.goto(DEMO_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)

        print("[2/4] Click reCAPTCHA anchor")
        try:
            page.frame_locator('iframe[title="reCAPTCHA"]').locator(
                "#recaptcha-anchor"
            ).click(timeout=10_000)
        except Exception as exc:
            print(f"FAIL: anchor click: {exc}")
            browser.close()
            return
        page.wait_for_timeout(2500)

        print("[3/4] inspect_visual_challenge_tool")
        result = inspect_visual_challenge_tool({"_page": page})
        printable = {k: v for k, v in result.items() if k != "screenshot_base64"}
        print(json.dumps(printable, indent=2, default=str))

        if "screenshot_base64" in result and result["screenshot_base64"]:
            b64 = result["screenshot_base64"]
            if "," in b64:
                b64 = b64.split(",", 1)[1]
            SCREENSHOT_OUT.write_bytes(base64.b64decode(b64))
            print(f"[4/4] Screenshot saved: {SCREENSHOT_OUT}")

        browser.close()


if __name__ == "__main__":
    main()
