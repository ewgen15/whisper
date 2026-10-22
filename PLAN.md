# Whisper — план розробки

## Статус: MVP запущений ✅

Голосовий щоденник. Записуєш голос → транскрипція → автоматична структуризація → зберігається в базу.

---

## Поточна архітектура

```
iPhone (Tailscale)
  │
  ├── PWA: https://ewgen15.github.io/whisper/
  │         GitHub Pages → docs/
  │
  └── Backend: https://homeserver.tail54ecf6.ts.net
                Tailscale Funnel + X-API-Key → міні PC → Docker
                ├── POST /transcribe  (faster-whisper, language)
                ├── POST /enrich      (gpt-4o правка + GPT-4o-mini поля, edit_level)
                ├── GET  /stats       ← documents (календар і графік настрою)
                ├── POST /save        → Supabase + Notion + вектор
                ├── GET  /entries     ← Supabase
                ├── PATCH/DELETE /entries/{id}  (синхронно з Notion і вектором)
                └── GET  /health
                          ↓
                    Supabase PostgreSQL
                    (entries, documents, embeddings)
```

---

## Що вже зроблено ✅

### Backend (міні PC, завжди онлайн)
- [x] FastAPI сервер в Docker (`restart: unless-stopped`)
- [x] `POST /transcribe` — faster-whisper small, CPU, int8
- [x] `POST /enrich` — GPT-4o-mini: назва, настрій, теги, тип, час доби
- [x] `POST /save` — зберігає в Supabase
- [x] `GET /entries` — читає з Supabase
- [x] Tailscale Funnel + API-ключ — HTTPS, публічний, захищений `X-API-Key`

### PWA (GitHub Pages)
- [x] Реальний запис голосу (MediaRecorder API)
- [x] Екран запису (таймер + waveform анімація)
- [x] Bottom sheet після зупинки (статус обробки)
- [x] Список записів (завантажується з Supabase)
- [x] Деталь запису (всі поля)
- [x] Пошук і фільтри по типу
- [x] Іконка + PWA manifest (встановлюється на Home Screen)
- [x] Налаштування (backend URL)
- [x] Fallback на localStorage якщо бекенд недоступний
- [x] Редагування і видалення запису (bottom sheet, підтвердження)
- [x] Картка: дата, проєкт, теги, індикатори синхронізації N/V
- [x] Журнал помилок і розширена діагностика в налаштуваннях
- [x] Налаштування мови запису (авто / укр / англ) і рівня правки тексту (без змін / легка / повна); сирий текст зберігається в `raw_text`
- [x] Правка слів за контекстом: окремий прохід `gpt-4o` у режимі «повна»
- [x] Різні емодзі й кольори для настроїв та станів (таблиці замість одного емодзі)
- [x] Екран «Статистика»: календар настрою (як contributions на GitHub, рік у підписах) + графік емоційних станів по місяцях (Chart.js), дані з `documents`
- [x] Екран не гасне під час запису (Wake Lock) + порятунок аудіо при обриві
- [x] Версія застосунку під лого (штамп `stamp-version.py`) і кнопка «Оновити застосунок»

### Інфраструктура
- [x] GitHub repo: `ewgen15/whisper` (monorepo: docs/ + backend/)
- [x] GitHub Pages з папки docs/
- [x] Supabase: таблиця `entries`
- [x] Tailscale на Mac + міні PC + iPhone

---

## Що далі

### Фаза 2 — CI/CD
- [x] GitHub Actions: push до backend/ → auto-deploy на міні PC (ненадійний, див. CLAUDE.md)
- [x] Tailscale OAuth + SSH keys в GitHub Secrets
- [x] CI: ruff + pytest + синтаксис фронтенда; деплой залежить від CI
- [x] Tailscale Funnel + `X-API-Key` (бекенд публічний)
- [ ] Автодеплой: полагодити Tailscale OAuth-секрети (`TS_OAUTH_CLIENT_ID`, `TS_OAUTH_SECRET`, `tag:ci`); поки деплой — `scripts/deploy.sh`
- [ ] Branch protection на `main` (обов'язкові перевірки `backend`, `frontend`, `docker`) — потрібен `gh auth login`
- [ ] Перший запуск `uptime.yml` вручну (Run workflow) і перевірка листа

### Фаза 3 — Заміна n8n-флоу (код готовий, вмикається прапорцями)
- [x] `POST /save` записує в Notion (`NOTION_ENABLED`, `NOTION_TOKEN`)
- [x] `POST /save` пише `documents` + `embeddings` ada-002 (`VECTOR_ENABLED`)
- [x] `NOTION_TOKEN` і прапорці в `.env` на сервері, протестовано
- [ ] Вимкнути відповідні кроки в n8n
- [x] `notion_page_id` і `document_id` зберігаються в `entries`
- [x] Редагування/видалення запису в застосунку синхронізується з Notion і векторами
- [x] Rate limit, docker-збірка в CI, uptime-алерт через GitHub issue

### Відомі проблеми / борг
- [x] Смужка внизу в iOS PWA (viewport 793 проти екрана 852): виправлено `fitScreenHeight` (`62ae813`), підтверджено на телефоні
- [ ] Кеш-фолбек `diary_entries`: локально збережені записи не повністю читаються `normalizeEntry` після перезавантаження
- [ ] PNG-іконки (`apple-touch-icon.png`, `icon-192/512.png`) досі старі, `icon.svg` новий
- [ ] Telegram-вхід не перенесений: для повної заміни n8n потрібен webhook `transcribe → enrich → save`
- [ ] Перевірити на iPhone: редагування, видалення, журнал помилок

### Покращення за оцінкою проєкту (пріоритет зверху вниз)
Функціонал:
- [ ] **Список записів із `documents`.** Зараз список читає `entries` (кілька записів), а статистика — `documents` (273). Історію n8n видно лише в календарі
- [ ] `POST /search` (див. Фазу 4) — вектори пишуться, але не використовуються
- [ ] Словник значень (розділ нижче)
- [ ] Надійність запису: що з аудіо, якщо бекенд недоступний або обробка впала — локальна черга з «повторити»
- [ ] Telegram-вхід (без нього n8n не замінити)
- [ ] Пізніше: експорт/бекап записів; web push-нагадування (iOS 16.4+ підтримує для PWA з Home Screen)

Технічне:
- [ ] **UI-тест** (Playwright, в'юпорт iPhone): головний екран, запис із підміненим бекендом, статистика; зловив би регресії на кшталт смужки внизу. Робимо наступним
- [ ] Одне головне сховище даних + колонка `source` (`app` / `n8n`); зараз `entries`, `documents` і Notion синхронізуються вручну
- [ ] Перевірити, як n8n пише `documents.created_at` (без часового поясу); привести до `timestamptz`, інакше нічні записи можуть потрапити не на той день
- [ ] Регулярний бекап (`pg_dump` на міні-PC): на free tier Supabase немає відновлення на момент часу
- [ ] Розбити `docs/index.html` (~1900 рядків) на `<script type="module">` без збірки
- [ ] Автодеплой: полагодити або прибрати й лишити `deploy.sh` (напівробочий гірший за відсутній)
- [ ] Оцінити витрати на `gpt-4o` у режимі «повна»; за потреби вмикати правку лише при низькій впевненості Whisper
- [ ] План відновлення, якщо міні-PC вийде з ладу (актуальна інструкція «з нуля» в `ARCHITECTURE.md`)
- [ ] Узгодити документи: шрифти й палітра (`PLAN.md` vs `SCREENS.md`), оновити `SCREENS.md` (екрани налаштувань, статистики, редагування) і закомітити

### Словник значень (теги, настрої та інші селекти)
Зараз модель вигадує теги й настрої вільно («штучний інтелект» поряд із наявним «ШІ», «натхнений» замість «натхненний»), а Notion при записі створює нові опції. Задум:
- [ ] Зберігати словник допустимих значень окремо в Supabase (джерело істини): теги, настрої, тип запису, емоційний/фізичний стан, час доби
- [ ] Промпт `enrichment.py` отримує словник із бази й вибирає значення з нього; нове значення додається у словник лише свідомо
- [ ] Notion лише приймає результат (запис значень), а не визначає словник; опції в Notion синхронізуються зі словником
- [ ] Початкове наповнення зі списків, що вже є в Notion, і з `entries`/`documents.metadata`
- Окремо: у `embeddings.metadata` старі записи n8n мають JSON-рядок, нові — об'єкт; привести до одного вигляду, якщо знадобиться фільтрація по метаданих

### Фаза 4 — Семантичний пошук
- [x] Генерувати embeddings після збереження (див. Фазу 3)
- [x] Зберігати в Supabase pgvector
- [ ] `POST /search` — семантичний пошук по записах

### Фаза 5 — Native iOS (SwiftUI)
- [ ] Той самий API
- [ ] `SFSpeechRecognizer` для live preview
- [ ] Нативні сповіщення, Shortcuts

---

## Технічний стек

| Компонент | Технологія |
|---|---|
| PWA | Vanilla HTML/JS, GitHub Pages |
| Backend | Python FastAPI, Docker |
| Транскрипція | faster-whisper (small, CPU) |
| Збагачення | OpenAI: gpt-4o (правка тексту), GPT-4o-mini (поля) |
| База даних | Supabase (PostgreSQL) |
| Мережа | Tailscale Funnel (публічний HTTPS) + `X-API-Key` |
| Хостинг | Fujitsu Esprimo Q556 (Ubuntu, домашній сервер) |
| Шрифти | Fixel Display (заголовки) + Fixel Text (UI) |
| Графіки | Chart.js 4.4.1 (cdnjs, ліниве завантаження) |
| Палітра | Peach `#FFCBA4` · Rose `#E8819A` · Lavender `#C080D8` · Plum `#1E1028` |
