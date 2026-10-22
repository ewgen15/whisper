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
                ├── POST /transcribe  (faster-whisper)
                ├── POST /enrich      (OpenAI GPT-4o-mini)
                ├── POST /save        → Supabase
                └── GET  /entries     ← Supabase
                          ↓
                    Supabase PostgreSQL
                    (таблиця entries)
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

### Фаза 3 — Заміна n8n-флоу (код готовий, вмикається прапорцями)
- [x] `POST /save` записує в Notion (`NOTION_ENABLED`, `NOTION_TOKEN`)
- [x] `POST /save` пише `documents` + `embeddings` ada-002 (`VECTOR_ENABLED`)
- [ ] Додати `NOTION_TOKEN` і прапорці в `.env` на сервері, перебудувати контейнер, протестувати без дублів з n8n
- [ ] Вимкнути відповідні кроки в n8n
- [ ] Зберігати `notion_page_id` в Supabase (потрібна колонка)

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
| Збагачення | OpenAI GPT-4o-mini |
| База даних | Supabase (PostgreSQL) |
| Мережа | Tailscale (private HTTPS) |
| Хостинг | Fujitsu Esprimo Q556 (Ubuntu, домашній сервер) |
| Шрифти | Fixel Display (заголовки) + Fixel Text (UI) |
| Палітра | Peach `#FFCBA4` · Rose `#E8819A` · Lavender `#C080D8` · Plum `#1E1028` |
