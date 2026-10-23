import re

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


SEED_DRAFT = """() => new Promise((resolve, reject) => {
  const req = indexedDB.open('whisper-draft', 1);
  req.onupgradeneeded = () => {
    req.result.createObjectStore('chunks', { autoIncrement: true });
    req.result.createObjectStore('meta');
  };
  req.onsuccess = () => {
    const tx = req.result.transaction(['chunks', 'meta'], 'readwrite');
    tx.objectStore('chunks').add({ seg: 0, buf: new Uint8Array(2048).buffer });
    tx.objectStore('meta').put({ mime: 'audio/webm', elapsed: 112 }, 'cur');
    tx.oncomplete = () => resolve(true);
    tx.onerror = () => reject(tx.error);
  };
})"""


def test_leftover_audio_is_recovered_and_processed(open_app):
    page = open_app()
    page.evaluate(SEED_DRAFT)
    page.reload()
    expect(page.locator("#sheetTitle")).to_have_text("Відновлена думка")
    expect(page.locator("#sheetDur")).to_have_text("1:52")
    expect(page.locator("#saveBtn")).to_have_text("Зберегти")


def test_retry_after_failed_upload_keeps_audio(open_app):
    calls = []

    def flaky(route):
        calls.append(1)
        cors = {"access-control-allow-origin": "*"}
        if len(calls) == 1:
            return route.fulfill(status=503, body="down", headers=cors)
        return route.fulfill(json={"text": "Привіт"}, headers=cors)

    page = open_app(transcribe=flaky)
    page.evaluate(SEED_DRAFT)
    page.reload()
    expect(page.locator("#saveBtn")).to_have_text("Повторити")
    expect(page.locator("#sheetSub")).to_contain_text("збережено на пристрої")
    page.locator("#saveBtn").click()
    expect(page.locator("#sheetTitle")).to_have_text("Відновлена думка")
    expect(page.locator("#saveBtn")).to_have_text("Зберегти")


def test_discard_removes_draft(open_app):
    page = open_app()
    page.evaluate(SEED_DRAFT)
    page.reload()
    expect(page.locator("#saveBtn")).to_have_text("Зберегти")
    page.get_by_text("Видалити запис").click()
    page.reload()
    page.wait_for_timeout(500)
    assert not page.locator("#sheet.up").count()


FAKE_MIC = """
Object.defineProperty(navigator, 'mediaDevices', {
  configurable: true,
  value: { getUserMedia: async () => ({ getTracks: () => [{ stop() {}, readyState: 'live' }] }) },
});
window.MediaRecorder = class {
  constructor() { this.state = 'inactive'; this.mimeType = 'audio/webm'; }
  start() {
    this.state = 'recording';
    this._t = setInterval(() => this.ondataavailable({ data: new Blob([new Uint8Array(64)]) }), 50);
  }
  stop() {
    clearInterval(this._t);
    this.state = 'inactive';
    this.ondataavailable({ data: new Blob([new Uint8Array(64)]) });
    setTimeout(() => this.onstop(), 0);
  }
};
"""


def test_pause_sends_segment_immediately_and_stop_sends_the_rest(open_app):
    calls = []

    def count(route):
        calls.append(1)
        route.fulfill(json={"text": f"частина {len(calls)}"}, headers={"access-control-allow-origin": "*"})

    page = open_app(transcribe=count, init_script=FAKE_MIC)
    page.locator("#recBtn").dispatch_event("click")
    expect(page.locator("#btnWrap")).to_have_class(re.compile("rec"))
    page.wait_for_timeout(300)

    page.locator("#pauseBtn").dispatch_event("click")
    expect(page.locator("#recState")).to_have_text("Пауза")
    expect(page.locator("#btnWrap")).to_have_class(re.compile("paused"))
    page.wait_for_timeout(500)
    assert len(calls) == 1, "first segment is transcribed on pause"

    page.locator("#pauseBtn").dispatch_event("click")
    expect(page.locator("#btnWrap")).not_to_have_class(re.compile("paused"))
    page.wait_for_timeout(300)
    page.locator("#recBtn").dispatch_event("click")
    expect(page.locator("#sheetTitle")).to_have_text("Відновлена думка")
    assert len(calls) == 2, "only the last segment is sent on stop"


def test_stop_while_paused_does_not_add_a_segment(open_app):
    calls = []

    def count(route):
        calls.append(1)
        route.fulfill(json={"text": "текст"}, headers={"access-control-allow-origin": "*"})

    page = open_app(transcribe=count, init_script=FAKE_MIC)
    page.locator("#recBtn").dispatch_event("click")
    expect(page.locator("#btnWrap")).to_have_class(re.compile("rec"))
    page.wait_for_timeout(300)
    page.locator("#pauseBtn").dispatch_event("click")
    expect(page.locator("#btnWrap")).to_have_class(re.compile("paused"))
    page.wait_for_timeout(300)
    page.locator("#recBtn").dispatch_event("click")
    expect(page.locator("#sheetTitle")).to_have_text("Відновлена думка")
    assert len(calls) == 1
