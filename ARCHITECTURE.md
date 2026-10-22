# Архітектура: приватний PWA з домашнім сервером

> Актуальні команди, прапорці, перевірки й підводні камені — у `CLAUDE.md`. Нижче — історія рішень; розділ про Tailscale описує початковий вибір (`serve`), зараз використовується Funnel + `X-API-Key`. Розділи «Модель даних» і «Приватність» — актуальні.
>
> Документ для передачі агенту. Описує рішення яке ми побудували і чому саме так.
> Можна використати як шаблон для схожих проектів.

---

## Задача

Мобільний додаток для особистого використання з:
- Записом голосу
- AI-обробкою (транскрипція + структуризація)
- Збереженням в базу
- Доступом тільки для власника

**Обмеження:** не хочемо платити за хмарний сервер, хочемо приватність.

---

## Ключові рішення

### 1. PWA замість нативного iOS

**Вибір:** Progressive Web App (HTML/JS) на GitHub Pages  
**Чому:** Нульова вартість хостингу, установка на Home Screen через Safari Share → "Add to Home Screen", HTTPS безкоштовно. Для MVP — ідеально.  
**Компроміс:** Немає нативних API iOS; мікрофон зупиняється при блокуванні екрана (лікується Wake Lock). Web push для PWA з Home Screen доступний з iOS 16.4, тож сповіщення можливі, але не зроблені.

### 2. Домашній сервер замість хмари

**Вибір:** Старий міні PC (Fujitsu Esprimo Q556, i5-7400T, 16GB, Ubuntu) вдома  
**Чому:** Вже є, завжди увімкнений, нульова вартість, швидкий faster-whisper локально.  
**Компроміс:** Залежить від домашнього інтернету та живлення. Для особистого використання — прийнятно.

### 3. Tailscale замість VPN/порт-форвардингу

**Вибір:** спочатку `tailscale serve` (лише для пристроїв у мережі), зараз **`tailscale funnel`** — адреса публічна в інтернеті. Захист: заголовок `X-API-Key`, rate limit (окремий лічильник для невдалих спроб), CORS.  
**Чому Funnel:** PWA на github.io з Chrome/інших мереж має дістатися бекенду без Tailscale на кожному пристрої; для Telegram-входу теж потрібна публічна адреса.  
**Чому взагалі Tailscale:**
- Жодного відкритого порту на роутері
- Автоматичний HTTPS сертифікат (вирішує mixed content з GitHub Pages)
- Одна команда: `tailscale funnel --bg 8000`
- Наслідок публічності: ключ `API_KEY` обов'язковий, його не можна вимикати

**URL:** `https://homeserver.tail54ecf6.ts.net` — стабільний, прив'язаний до машини

**Компроміс:** Потрібен Tailscale на всіх пристроях. Не підходить для продакшн з багатьма користувачами.

### 4. Docker на сервері

**Вибір:** Docker Compose з `restart: unless-stopped`  
**Чому:** Ізоляція залежностей (ffmpeg, Python, faster-whisper), автостарт після ребуту, легко оновлювати через `git pull && docker compose up -d --build`.

### 5. faster-whisper на CPU

**Вибір:** `WhisperModel("small", device="cpu", compute_type="int8")`  
**Чому:** i5-7400T без GPU. Small модель (~480MB) дає ~25 секунд на 2 хвилини аудіо — прийнятно для щоденника. int8 квантизація прискорює в 2-3 рази.  
**Компроміс:** Large модель надто повільна на CPU.

### 6. Розділення /transcribe і /enrich

**Вибір:** Два окремих ендпоінти замість одного  
**Чому:** Модульність. Можна замінити Whisper на інший сервіс, не чіпаючи збагачення. Легко тестувати окремо. PWA бачить прогрес ("Транскрибується..." → "Аналізується...").

### 7. Supabase як база

**Вибір:** Supabase PostgreSQL (таблиці `entries`, `documents`, `embeddings`)  
**Чому:** Вже використовується в проекті для pgvector. Free tier достатній. Записи доступні з будь-якого пристрою (не тільки localStorage одного браузера).  
**Схема:** Всі операції через бекенд (service_role key на сервері, не у клієнті).

### 8. Monorepo структура

```
ewgen15/whisper (GitHub)
├── docs/          ← PWA (GitHub Pages серверить звідси)
│   ├── index.html
│   ├── manifest.json
│   └── icon-*.png
├── scripts/       ← check-frontend, stamp-version, deploy.sh
├── .github/workflows/  ← ci, deploy-backend, uptime
└── backend/       ← FastAPI сервер
    ├── app/
    │   ├── main.py          роути, middleware (PNA, CORS, Guard)
    │   ├── transcription.py faster-whisper
    │   ├── enrichment.py    промпт, правка тексту (gpt-4o), поля (gpt-4o-mini)
    │   ├── storage.py       entries, читання для статистики
    │   ├── integrations.py  documents/embeddings + Notion
    │   ├── stats.py         агрегація по днях
    │   └── ratelimit.py
    ├── tests/               pytest, зовнішні сервіси замоковані
    ├── Dockerfile
    ├── docker-compose.yml
    └── requirements.txt
```

**Чому:** Один репо — простіше керувати. GitHub Pages з `/docs` — стандартна практика.

---

## Модель даних

Дані живуть у трьох місцях, повного «головного» сховища немає (див. борг в `PLAN.md`):

| Сховище | Хто пише | Хто читає | Примітки |
|---|---|---|---|
| `entries` (Supabase) | застосунок (`/save`, PATCH, DELETE) | список у застосунку | зберігає `raw_text`, `notion_page_id`, `document_id` |
| `documents` + `embeddings` | n8n і застосунок | `GET /stats`, (майбутній) семантичний пошук | `created_at` без часового поясу; `embeddings.metadata` у старих рядках — JSON-рядок, у нових — об'єкт |
| Notion «Особистий щоденник» | n8n і застосунок | людина | select-значення мають збігатися з наявними опціями |

Записи не позначені джерелом. Формат рядка для embedding і модель ada-002 мають лишатися як в n8n.

## Конвеєр обробки запису

```
аудіо ─► /transcribe (Whisper small, language?) ─► raw_text
      ─► /enrich:  edit_level=full  → gpt-4o правка слів за контекстом
                   (none/light      → без проходу gpt-4o)
                   → gpt-4o-mini: title, mood, tags, … (time_of_day рахує код, Київ)
      ─► /save: entries → documents/embeddings → Notion (збій синхронізації не ламає запис)
```

## Приватність

Тексти щоденника залишають домашній сервер: аудіо розпізнається локально, але **текст іде в OpenAI або Anthropic** (правка й розбір; вибір у налаштуваннях) та в OpenAI (embeddings). Ключі LLM із налаштувань лежать у `localStorage` телефона й передаються на бекенд заголовком (лише для `/enrich`, ніде не логуються), у **Supabase** і **Notion**. Секрети лише в `backend/.env` на сервері. Бекенд публічний, тому весь доступ іде через `X-API-Key`.

---

## Секрети і credentials

| Секрет | Де живе | Як передається |
|---|---|---|
| `OPENAI_API_KEY` | `backend/.env` на міні PC | Вручну по SSH, не в git |
| `SUPABASE_URL` | `backend/.env` на міні PC | Вручну по SSH |
| `SUPABASE_KEY` | `backend/.env` на міні PC | Вручну по SSH |
| `VECTOR_ENABLED`, `NOTION_ENABLED` | `backend/.env` | прапорці синхронізації (за замовчуванням вимкнені) |
| `API_KEY` | `backend/.env` + налаштування застосунку | Вручну; заголовок `X-API-Key` |
| `NOTION_TOKEN` | `backend/.env` на міні PC | Вручну по SSH |

`.env` в `.gitignore` — в GitHub нічого секретного немає.

---

## Деплой нової версії

**PWA (автоматично):**
```bash
# На Mac, зміни в docs/
git add docs/ && git commit -m "..." && git push
# GitHub Pages оновлюється за ~1 хв
```

**Backend (зараз вручну):**
```bash
# На Mac — пушимо зміни
git push

# Зручніше: ./scripts/deploy.sh. Вручну на міні PC (через SSH):
cd ~/whisper && git pull origin main
cd backend && docker compose down && docker compose up -d --build
```

Автодеплой: `.github/workflows/deploy-backend.yml` (після CI), але може не перебудувати контейнер — див. `CLAUDE.md`.

---

## Як відтворити з нуля

### Що потрібно
1. Будь-який старий PC (4+ ядра, 8+ GB RAM) з Ubuntu/Debian
2. GitHub акаунт
3. Supabase акаунт (free tier)
4. Tailscale акаунт (free tier)
5. OpenAI API key

### Кроки

**1. Міні PC — базове налаштування:**
```bash
# Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER && newgrp docker

# Tailscale
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
sudo tailscale set --operator=$USER
tailscale funnel --bg 8000   # публічно: обов'язково задай API_KEY в .env
# → отримуєш HTTPS URL типу https://machinename.tailXXXXX.ts.net
```

**2. GitHub — створити репо:**
- Новий публічний репо
- Settings → Pages → Branch: main → /docs

**3. Клонувати і налаштувати:**
```bash
# На міні PC
git clone https://github.com/USERNAME/REPO.git ~/whisper
cat > ~/whisper/backend/.env << EOF
OPENAI_API_KEY=sk-...
SUPABASE_URL=https://XXXXX.supabase.co
SUPABASE_KEY=eyJ...
API_KEY=<довгий-випадковий-ключ>
# за потреби: VECTOR_ENABLED=true, NOTION_ENABLED=true, NOTION_TOKEN=...
EOF
cd ~/whisper/backend && docker compose up -d --build
```

**4. Supabase — створити таблицю** (SQL Editor):
```sql
CREATE TABLE IF NOT EXISTS entries (
  id BIGSERIAL PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  title TEXT, text TEXT, raw_text TEXT,
  mood TEXT[], emotional_state TEXT, physical_state TEXT,
  context TEXT, project TEXT, tags TEXT[],
  entry_type TEXT, time_of_day TEXT, duration TEXT,
  date_label TEXT, month_label TEXT,
  notion_page_id TEXT, document_id BIGINT
);
```
Для векторів і статистики потрібні також `documents` та `embeddings` (pgvector, `vector(1536)`) — вони вже є у проекті «Vector AI Store» і спільні з n8n.

**5. PWA — задати backend URL і API-ключ** у налаштуваннях застосунку (зберігаються в `localStorage` пристрою; значення за замовчуванням у `docs/index.html` — лише запасне).

**6. iPhone:**
- Встановити Tailscale, зайти в той самий акаунт
- Safari → https://USERNAME.github.io/REPO/ → Share → Add to Home Screen

---

## Вартість

| Сервіс | Вартість |
|---|---|
| GitHub Pages | Безкоштовно |
| Supabase | Безкоштовно (free tier) |
| Tailscale | Безкоштовно (до 3 пристроїв на free tier, 100 на personal) |
| Whisper | локально на міні PC (0 $) |
| OpenAI GPT-4o-mini (поля) | ~$0.001 за запис |
| OpenAI gpt-4o (правка тексту, режим «повна») | найдорожчий крок; перевір реальні витрати |
| OpenAI ada-002 (вектори) | копійки |
| Електрика (міні PC) | ~€3-5/місяць (35W TDP) |

**Загалом: ~€3-5/місяць** для активного щоденного використання.
