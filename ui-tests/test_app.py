import re

from conftest import ENRICHED
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
    expect(page.locator("#rvTitle")).to_have_value("Відновлена думка")
    expect(page.locator("#rvDur")).to_have_text("1:52")
    expect(page.locator("#rvSave")).to_have_text("Зберегти")


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
    expect(page.locator("#rvSave")).to_have_text("Повторити")
    expect(page.locator("#rvErrMsg")).to_contain_text("збережено на пристрої")
    page.locator("#rvSave").click()
    expect(page.locator("#rvTitle")).to_have_value("Відновлена думка")
    expect(page.locator("#rvSave")).to_have_text("Зберегти")


def test_discard_removes_draft(open_app):
    page = open_app()
    page.evaluate(SEED_DRAFT)
    page.reload()
    expect(page.locator("#rvSave")).to_have_text("Зберегти")
    page.locator("#rvClose").click()
    page.locator("#dcOk").click()
    expect(page.locator("#review")).not_to_have_class(re.compile("active"))
    page.reload()
    page.wait_for_timeout(500)
    assert not page.locator("#review.active").count()


FAKE_MIC = """
Object.defineProperty(navigator, 'mediaDevices', {
  configurable: true,
  value: { getUserMedia: async () => {
    window.__mics = (window.__mics || 0) + 1;
    return { getTracks: () => [{ stop() {}, readyState: 'live', get muted() { return !!window.__muted; } }] };
  } },
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
    expect(page.locator("#rvTitle")).to_have_value("Відновлена думка")
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
    expect(page.locator("#rvTitle")).to_have_value("Відновлена думка")
    assert len(calls) == 1


def test_muted_mic_is_shown_and_recovered(open_app):
    page = open_app(init_script=FAKE_MIC)
    page.locator("#recBtn").dispatch_event("click")
    expect(page.locator("#btnWrap")).to_have_class(re.compile("rec"))
    expect(page.locator("#recState")).to_have_text("Запис")

    page.evaluate("window.__muted = true")
    expect(page.locator("#recState")).to_have_text("Мікрофон не чує", timeout=5000)

    page.evaluate("window.__muted = false")
    expect(page.locator("#recState")).to_have_text("Запис", timeout=5000)
    assert page.evaluate("window.__mics") >= 2, "mic is reopened after it went silent"


def test_resume_reopens_the_mic(open_app):
    page = open_app(init_script=FAKE_MIC)
    page.locator("#recBtn").dispatch_event("click")
    expect(page.locator("#btnWrap")).to_have_class(re.compile("rec"))
    page.locator("#pauseBtn").dispatch_event("click")
    expect(page.locator("#btnWrap")).to_have_class(re.compile("paused"))
    page.locator("#pauseBtn").dispatch_event("click")
    expect(page.locator("#btnWrap")).not_to_have_class(re.compile("paused"))
    assert page.evaluate("window.__mics") == 2


def review_ready(open_app):
    page = open_app()
    page.evaluate(SEED_DRAFT)
    page.reload()
    expect(page.locator("#rvSave")).to_be_enabled()
    return page


def test_review_is_a_full_screen_not_a_sheet(open_app):
    page = review_ready(open_app)
    expect(page.locator("#review")).to_have_class(re.compile("active"))
    expect(page.locator("#btnAnchor")).not_to_have_class(re.compile("visible"))
    box = page.locator("#review").bounding_box()
    vp = page.viewport_size
    assert box["width"] >= vp["width"] - 1 and box["height"] >= vp["height"] - 1
    expect(page.locator("#rvText")).to_have_value("Привіт, світе.")
    expect(page.locator("#rvMood")).to_contain_text("Спокійний")
    expect(page.locator("#rvTags")).to_contain_text("тест")
    # nothing overflows sideways and the action bar stays inside the screen
    assert page.evaluate("document.documentElement.scrollWidth - window.innerWidth") <= 0
    bar = page.locator(".rv-bar").bounding_box()
    assert bar["y"] + bar["height"] <= vp["height"]


def test_save_sends_edited_title_text_and_chips(open_app):
    page = review_ready(open_app)
    page.locator("#rvTitle").fill("Моя назва")
    page.locator("#rvText").fill("Виправлений текст.")
    page.locator("#rvTags .rv-del").click()
    page.locator("#rvTags .rv-add").click()
    page.locator(".rv-add-in").fill("новий")
    page.locator(".rv-add-in").press("Enter")
    expect(page.locator("#rvTags")).to_contain_text("новий")
    page.locator("#rvSave").click()
    expect(page.locator("#review")).not_to_have_class(re.compile("active"))
    assert len(page.saves) == 1
    sent = page.saves[0]
    assert sent["title"] == "Моя назва"
    assert sent["text"] == "Виправлений текст."
    assert sent["rawText"] == "Привіт"
    assert sent["tags"] == ["новий"]
    assert sent["mood"] == ["Спокійний"]
    expect(page.locator("#homeEntries").get_by_text("Моя назва")).to_be_visible()


def test_raw_text_toggle_is_read_only_and_keeps_edits(open_app):
    page = review_ready(open_app)
    page.locator("#rvText").fill("Моя правка")
    page.locator("#rvSeg").get_by_text("Оригінал").click()
    expect(page.locator("#rvText")).to_have_value("Привіт")
    expect(page.locator("#rvText")).not_to_be_editable()
    page.locator("#rvSeg").get_by_text("Оброблений").click()
    expect(page.locator("#rvText")).to_have_value("Моя правка")


def test_removing_a_mood_chip_is_saved(open_app):
    page = review_ready(open_app)
    page.locator("#rvMood .rv-del").click()
    page.locator("#rvSave").click()
    expect(page.locator("#review")).not_to_have_class(re.compile("active"))
    assert page.saves[0]["mood"] == []


def scale(page, key, value):
    page.locator(f'#rvScales button[data-k="{key}"][data-v="{value}"]').click()


def test_scales_start_from_the_models_guess_and_show_the_derived_state(open_app):
    page = review_ready(open_app)
    for key, value in (("valence", "1"), ("energy", "0"), ("control", "1")):
        expect(page.locator(f'#rvScales button[data-k="{key}"][data-v="{value}"]')).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#rvScales button.pred")).to_have_count(3)
    expect(page.locator("#rvDerived")).to_contain_text("Гарний")
    expect(page.locator("#rvDerived")).to_contain_text("стан за трьома шкалами")


def test_correcting_the_scales_changes_the_state_and_saves_both_values(open_app):
    page = review_ready(open_app)
    scale(page, "valence", "-1")
    scale(page, "control", "-2")
    expect(page.locator("#rvDerived")).to_contain_text("Пригнічений")
    expect(page.locator("#rvDerived")).to_contain_text("модель вгадала «Гарний»")
    page.locator("#rvSave").click()
    expect(page.locator("#review")).not_to_have_class(re.compile("active"))
    sent = page.saves[0]
    assert (sent["valence"], sent["energy"], sent["control"]) == (-1, 0, -2)
    assert (sent["pred_valence"], sent["pred_energy"], sent["pred_control"]) == (1, 0, 1)
    assert sent["emotional_state"] == "Пригнічений"


def test_without_scales_from_the_backend_the_user_sets_them_and_saving_still_works(open_app):
    enriched = {k: v for k, v in ENRICHED.items() if k not in ("valence", "energy", "control")}
    page = open_app(enriched=enriched)
    page.evaluate(SEED_DRAFT)
    page.reload()
    expect(page.locator("#rvSave")).to_be_enabled()
    expect(page.locator("#rvDerived")).to_contain_text("Задай усі три шкали")
    expect(page.locator("#rvScales button.pred")).to_have_count(0)
    page.locator("#rvSave").click()  # nothing set: the model's own label (if any) is kept
    expect(page.locator("#review")).not_to_have_class(re.compile("active"))
    assert page.saves[0]["valence"] is None and page.saves[0]["pred_valence"] is None
    assert page.saves[0]["emotional_state"] == "Гарний"


def test_close_asks_before_deleting_and_can_be_cancelled(open_app):
    page = review_ready(open_app)
    page.locator("#rvClose").click()
    expect(page.locator("#discardSheet")).to_have_class(re.compile("up"))
    page.get_by_role("button", name="Залишити").click()
    expect(page.locator("#discardSheet")).not_to_have_class(re.compile("up"))
    expect(page.locator("#review")).to_have_class(re.compile("active"))
    assert page.evaluate(SEED_CHECK) is True, "draft survives a cancelled discard"


def test_processing_state_keeps_save_disabled(open_app):
    def slow(route):
        page.wait_for_timeout(1500)
        route.fulfill(json={"text": "Привіт"}, headers={"access-control-allow-origin": "*"})

    page = open_app(transcribe=slow)
    page.evaluate(SEED_DRAFT)
    page.reload()
    expect(page.locator("#review")).to_have_attribute("data-s", "transcribing")
    expect(page.locator("#rvSave")).to_be_disabled()
    expect(page.locator("#review")).to_have_attribute("data-s", "ready", timeout=8000)
    expect(page.locator("#rvSave")).to_be_enabled()


SEED_CHECK = """() => new Promise(resolve => {
  const req = indexedDB.open('whisper-draft', 1);
  req.onsuccess = () => {
    const q = req.result.transaction('chunks').objectStore('chunks').count();
    q.onsuccess = () => resolve(q.result > 0);
  };
})"""
