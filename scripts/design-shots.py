#!/usr/bin/env python3
"""Screenshots of every main screen (iPhone 14, WebKit, mocked backend) into design/screens/<label>/.

    python3 scripts/design-shots.py before      # → design/screens/before/01-home.png …
    python3 scripts/design-shots.py after

Needs the UI-test setup:  pip install -r ui-tests/requirements.txt && playwright install webkit
design/ is git-ignored, so the shots stay local.
"""
import functools
import http.server
import json
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
BACKEND = "https://backend.test"
CORS = {"access-control-allow-origin": "*"}

NOW = datetime(2026, 10, 27, 9, 30)


def entry(i, days_ago, hour, title, text, mood, emo, phys, etype, tod, tags, dur, context="", project=None):
    when = (NOW - timedelta(days=days_ago)).replace(hour=hour, minute=12)
    return {"id": i, "title": title, "text": text, "mood": mood, "emotional_state": emo, "physical_state": phys,
            "entry_type": etype, "time_of_day": tod, "tags": tags, "duration": dur, "context": context, "project": project,
            "created_at": when.isoformat() + "Z", "notion_page_id": f"n{i}" if i % 2 else None,
            "document_id": f"d{i}" if i % 3 else None,
            "date_label": when.strftime("%-d %B %Y, %H:%M").replace("October", "жовтня"),
            "month_label": "Жовтень 2026"}


ENTRIES = [
    entry(8, 0, 8, "Опалення нарешті запрацювало", "Вчора був дуже засмучений, що весь день пішов на опалення та насос.\n\nАле зрештою ми його включили. Зараз буду дивитися, що за ніч нагріло.",
          ["розчарований", "втомлений"], "Нейтральний", "Втомлений", "Рефлексія", "Ранок", ["опалення", "ремонт", "насос"], "1:12", "Налаштування опалення в будинку", "Ремонт будинку"),
    entry(7, 1, 21, "Ідея формату роботи з дизайном", "Виникла ідея зробити інший формат роботи з дизайном: спочатку прототип, потім список компонентів, потім переводимо у Фігму.",
          ["натхненний", "зацікавлений"], "Позитивний", "Бадьорий", "Ідея", "Вечір", ["дизайн", "figma", "процес"], "2:03"),
    entry(6, 2, 14, "Судове засідання знову перенесли", "Суддя пішов на лікарняний, і засідання переноситься мінімум на два тижні. Це відтягує продаж квартири.",
          ["пригнічений", "тривожний"], "Пригнічений", "Нейтральний", "Подія", "День", ["суд", "квартира"], "0:48", "Розмова з адвокатом"),
    entry(5, 4, 7, "План на тиждень", "Доробити електрику на другому поверсі, потім сантехніка, потім можна класти плитку.",
          ["спокійний"], "Нейтральний", "Нейтральний", "Планування", "Ранок", ["електрика", "плитка"], "1:30", "", "Ремонт будинку"),
    entry(4, 9, 18, "Приснився цікавий сон", "Я одночасно був учасником та спостерігачем. Здається, це образ того майбутнього стану, в якому я хотів би колись перебувати.",
          ["ностальгічний", "піднесений"], "Позитивний", "Гарний", "Сон", "Вечір", ["сон"], "0:55"),
    entry(3, 14, 12, "Зустріч із друзями у хабі", "Основний висновок: ідея підкріплює бажання робити чайні зустрічі.",
          ["задоволений"], "Гарний", "Нейтральний", "Подія", "День", ["спільнота", "зустрічі"], "1:05"),
]


# a few entries carry the three mood scales, the model's own guess and saved audio (as entries saved after the scales feature do)
for e, sc, pred in ((8, (-1, -1, 0), (-1, 0, 0)), (7, (1, 1, 1), (1, 1, 1)), (6, (-1, -1, -2), (-1, 0, -1))):
    row = next(x for x in ENTRIES if x["id"] == e)
    row.update(valence=sc[0], energy=sc[1], control=sc[2], pred_valence=pred[0], pred_energy=pred[1], pred_control=pred[2],
               audio_path=f"2026/10/{e:032x}.opus", emotional_state={8: "Поганий", 7: "Позитивний", 6: "Пригнічений"}[e])


def stats_payload():
    import random
    rnd = random.Random(3)
    moods = [["зацікавлений", "натхненний"], ["спокійний"], ["тривожний"], ["втомлений", "розчарований"], ["радісний"], ["замислений"]]
    emos = ["Позитивний", "Гарний", "Нейтральний", "Поганий", "Пригнічений"]
    days = []
    for k in range(200, -1, -1):
        if rnd.random() < 0.35:
            continue
        d = (NOW - timedelta(days=k)).date().isoformat()
        n = rnd.choice([1, 1, 1, 2, 3])
        days.append({"d": d, "n": n, "mood": [m for _ in range(n) for m in rnd.choice(moods)],
                     "emo": [rnd.choices(emos, [5, 3, 3, 1, 1])[0] for _ in range(n)]})
    return {"total": sum(x["n"] for x in days), "days": days}


ENRICHED = {"title": "Відновлена думка", "text": "Сьогодні нарешті розібрався з опаленням, все працює. Дуже задоволений результатом, але ще треба перевірити за ніч.",
            "mood": ["задоволений", "втомлений"], "valence": 1, "energy": 0, "control": 1, "emotional_state": "Гарний",
            "physical_state": "Втомлений", "entry_type": "Подія", "time_of_day": "Ранок", "context": "Налаштування опалення",
            "project": "Ремонт будинку", "tags": ["опалення", "ремонт"]}

SEED_DRAFT = """() => new Promise((resolve, reject) => {
  const req = indexedDB.open('whisper-draft', 1);
  req.onupgradeneeded = () => { req.result.createObjectStore('chunks', { autoIncrement: true }); req.result.createObjectStore('meta'); };
  req.onsuccess = () => {
    const tx = req.result.transaction(['chunks', 'meta'], 'readwrite');
    tx.objectStore('chunks').add({ seg: 0, buf: new Uint8Array(2048).buffer });
    tx.objectStore('meta').put({ mime: 'audio/webm', elapsed: 112 }, 'cur');
    tx.oncomplete = () => resolve(true); tx.onerror = () => reject(tx.error);
  };
})"""

FAKE_MIC = """
Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia: async () =>
  ({ getTracks: () => [{ stop() {}, readyState: 'live', muted: false }] }) } });
window.MediaRecorder = class {
  constructor() { this.state = 'inactive'; this.mimeType = 'audio/webm'; }
  start() { this.state = 'recording'; this._t = setInterval(() => this.ondataavailable({ data: new Blob([new Uint8Array(64)]) }), 50); }
  stop() { clearInterval(this._t); this.state = 'inactive'; this.ondataavailable({ data: new Blob([new Uint8Array(64)]) }); setTimeout(() => this.onstop(), 0); }
};
"""


def main(label):
    out = ROOT / "design" / "screens" / label
    out.mkdir(parents=True, exist_ok=True)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT / "docs"))
    handler.log_message = lambda *a, **k: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    site = f"http://127.0.0.1:{server.server_port}"

    def backend(down=False, slow_transcribe=False):
        def handle(route):
            url = route.request.url
            if route.request.method == "OPTIONS":
                return route.fulfill(status=204, headers=CORS)
            if down:
                return route.fulfill(status=503, body="down", headers=CORS)
            if url.endswith("/entries"):
                return route.fulfill(json=ENTRIES, headers=CORS)
            if url.endswith("/stats"):
                return route.fulfill(json=stats_payload(), headers=CORS)
            if url.endswith("/transcribe"):
                if slow_transcribe:
                    return  # never answers: keeps the review screen in its «transcribing» state
                return route.fulfill(json={"text": ENRICHED["text"]}, headers=CORS)
            if url.endswith("/enrich"):
                return route.fulfill(json=ENRICHED, headers=CORS)
            return route.fulfill(status=503, body="down", headers=CORS)
        return handle

    n = [0]

    def shot(page, name):
        n[0] += 1
        page.wait_for_timeout(1000)
        page.screenshot(path=str(out / f"{n[0]:02d}-{name}.png"))
        print(f"{n[0]:02d}-{name}")

    with sync_playwright() as p:
        browser = p.webkit.launch()

        def fresh(**kw):
            ctx = browser.new_context(**p.devices["iPhone 14"], service_workers="block", locale="uk-UA")
            page = ctx.new_page()
            page.route(f"{BACKEND}/**", backend(**kw))
            page.add_init_script(f"localStorage.setItem('diary_backend', {json.dumps(BACKEND)}); localStorage.setItem('diary_api_key', 'k');")
            return page

        page = fresh()
        page.goto(site + "/index.html")
        page.wait_for_selector("#homeEntries .entry-card, #homeEntries *", timeout=8000)
        shot(page, "home")
        page.locator("#showAll").click()
        shot(page, "list")
        page.locator("#searchInput").fill("нічого-такого")
        shot(page, "list-empty-search")
        page.locator("#searchInput").fill("")
        page.evaluate("openDetail(8)")
        shot(page, "detail")
        page.evaluate("openEdit()")
        shot(page, "edit-sheet")
        page.reload()
        page.evaluate("openStats()")
        page.wait_for_timeout(1800)
        shot(page, "stats")
        page.reload()
        page.evaluate("openSettings()")
        shot(page, "settings-sheet")
        page.evaluate("document.querySelector('#settingsSheet').scrollTo(0, 99999)")
        shot(page, "settings-sheet-bottom")
        page.context.close()

        page = fresh()
        page.evaluate("0")
        page.goto(site + "/index.html")
        page.evaluate(SEED_DRAFT)
        page.reload()
        page.wait_for_selector("#rvSave:not([disabled])", timeout=8000)
        shot(page, "review-ready")
        page.evaluate("document.querySelector('#review .rv-body').scrollTo(0, 99999)")
        shot(page, "review-ready-bottom")
        page.evaluate("askDiscard('delete')")
        shot(page, "review-discard-sheet")
        page.context.close()

        page = fresh(slow_transcribe=True)
        page.goto(site + "/index.html")
        page.evaluate(SEED_DRAFT)
        page.reload()
        shot(page, "review-transcribing")
        page.context.close()

        page = fresh()
        page.add_init_script(FAKE_MIC)
        page.goto(site + "/index.html")
        page.wait_for_timeout(500)
        page.locator("#recBtn").dispatch_event("click")
        page.wait_for_timeout(1500)
        shot(page, "recording")
        page.locator("#pauseBtn").dispatch_event("click")
        shot(page, "recording-paused")
        page.context.close()

        page = fresh(down=True)
        page.goto(site + "/index.html")
        page.wait_for_timeout(1200)
        shot(page, "home-backend-down")
        browser.close()
    server.shutdown()
    print("saved to", out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "before")
