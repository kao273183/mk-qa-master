from playwright.sync_api import Page, expect


def test_homepage_title(page: Page):
    page.goto("https://example.com")
    expect(page).to_have_title("Example Domain")


def test_link_visible(page: Page):
    page.goto("https://example.com")
    # example.com renamed its only link from "More information..." to
    # "Learn more" (reported in issue #34). Match the stable href instead
    # of the visible copy so a future text tweak doesn't break this again.
    expect(page.locator('a[href*="iana.org"]')).to_be_visible()
