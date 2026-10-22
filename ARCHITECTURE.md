# Архітектура: приватний PWA з домашнім сервером

> Актуальні команди, прапорці, перевірки й підводні камені — у `CLAUDE.md`. Нижче — історія рішень; розділ про Tailscale описує початковий вибір (`serve`), зараз використовується Funnel + `X-API-Key`.
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
**Компроміс:** Немає нативних сповіщень, обмежений доступ до мікрофону (але MediaRecorder API працює в Safari 14.5+).

### 2. Домашній сервер замість хмари

**Вибір:** Старий міні PC (Fujitsu Esprimo Q556, i5-7400T, 16GB, Ubuntu) вдома  
**Чому:** Вже є, завжди увімкнений, нульова вартість, швидкий faster-whisper локально.  
**Компроміс:** Залежить від домашнього інтернету та живлення. Для особистого використання — прийнятно.

### 3. Tailscale замість VPN/порт-форвардингу

**Вибір:** Tailscale serve для HTTPS до домашнього сервера  
**Чому:**
- Жодного відкритого порту на роутері
- Автоматичний HTTPS сертифікат (вирішує mixed content з GitHub Pages)
- Доступно тільки пристроям в Tailscale мережі (Mac, iPhone, міні PC)
- `tailscale serve --bg http://localhost:8000` — одна команда

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

**Вибір:** Supabase PostgreSQL (таблиця `entries`)  
**Чому:** Вже використовується в проекті для pgvector. Free tier достатній. Записи доступні з будь-якого пристрою (не тільки localStorage одного браузера).  
**Схема:** Всі операції через бекенд (service_role key на сервері, не у клієнті).

### 8. Monorepo структура

```
ewgen15/whisper (GitHub)
├── docs/          ← PWA (GitHub Pages серверить звідси)
│   ├── index.html
│   ├── manifest.json
│   └── icon-*.png
└── backend/       ← FastAPI сервер
    ├── app/
    │   ├── main.py
    │   ├── transcription.py
    │   ├── enrichment.py
    │   └── storage.py
    ├── Dockerfile
    ├── docker-compose.yml
    └── requirements.txt
```

**Чому:** Один репо — простіше керувати. GitHub Pages з `/docs` — стандартна практика.

---

## Секрети і credentials

| Секрет | Де живе | Як передається |
|---|---|---|
| `OPENAI_API_KEY` | `backend/.env` на міні PC | Вручну по SSH, не в git |
| `SUPABASE_URL` | `backend/.env` на міні PC | Вручну по SSH |
| `SUPABASE_KEY` | `backend/.env` на міні PC | Вручну по SSH |
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

# На міні PC (або через SSH)
cd ~/whisper && git pull origin main
cd backend && docker compose up -d --build
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
tailscale serve --bg http://localhost:8000
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
  date_label TEXT, month_label TEXT
);
```

**5. PWA — оновити backend URL** в `docs/index.html`:
```js
return (localStorage.getItem('diary_backend') || 'https://YOUR-MACHINE.tailXXXX.ts.net')
```

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
| OpenAI Whisper | ~$0.006/хв аудіо |
| OpenAI GPT-4o-mini | ~$0.001 за запис |
| Електрика (міні PC) | ~€3-5/місяць (35W TDP) |

**Загалом: ~€3-5/місяць** для активного щоденного використання.
