from playwright.sync_api import Page, expect


def test_homepage_title(page: Page):
    page.goto("https://example.com")
    expect(page).to_have_title("Example Domain")


def test_link_visible(page: Page):
    page.goto("https://example.com")
    expect(page.get_by_role("link", name="More information...")).to_be_visible()
