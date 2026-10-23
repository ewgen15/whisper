from playwright.sync_api import expect

CARDS = "#listEntries .entry-card"


def test_list_shows_entries_from_backend(open_app):
    page = open_app()
    expect(page.locator(CARDS)).to_have_count(2)
    expect(page.locator("#listEntries").get_by_text("Ранкова прогулянка")).to_be_visible()
    expect(page.locator("#backendBanner")).to_be_hidden()


def test_banner_when_backend_is_down(open_app):
    page = open_app(entries=None)
    expect(page.locator("#backendBanner")).to_be_visible()


def test_no_horizontal_scroll_on_phone(open_app):
    page = open_app()
    expect(page.locator(CARDS).first).to_be_attached()
    overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    assert overflow <= 0
