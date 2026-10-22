# Whisper — голосовий щоденник (інструкція для агентів)

Прочитай цей файл першим. Деталі: `ARCHITECTURE.md` (чому так побудовано), `PLAN.md` (статус і roadmap), `SCREENS.md` (UX-специфікація екранів).

## Що це

PWA українською: користувач диктує думку → Whisper транскрибує → GPT розбирає на поля → запис зберігається в Supabase (і, коли ввімкнено, у Notion + вектор для семантичного пошуку). Проєкт поступово **замінює n8n-флоу** щоденника.

**Це мобільний застосунок** (iPhone, PWA з Home Screen). Будь-яку UI-зміну оцінюй на малому екрані та з `env(safe-area-inset-*)`.

## Архітектура

```
iPhone/Mac ──► PWA  https://ewgen15.github.io/whisper/   (GitHub Pages, папка docs/)
                │  fetch + заголовок X-API-Key
                ▼
        https://homeserver.tail54ecf6.ts.net   (Tailscale Funnel → публічний!)
                ▼
   Міні-PC (Ubuntu, Docker, ~/whisper/backend): FastAPI
      POST /transcribe   faster-whisper small, CPU
      POST /enrich       GPT-4o-mini → JSON з полями запису
      POST /save         entries (Supabase) + sync: documents/embeddings + Notion
      GET  /entries      читання з Supabase
      GET  /health       без авторизації
```

| Шар | Де | Примітки |
|---|---|---|
| Фронтенд | `docs/index.html` | Один файл, vanilla HTML/CSS/JS, без збірки. `docs/` = корінь GitHub Pages. Інших `.md` у `docs/` не клади — вони стануть публічними |
| Бекенд | `backend/app/` | `main.py` (роути, middleware), `transcription.py`, `enrichment.py` (промпт), `storage.py` (`entries`), `integrations.py` (вектор + Notion) |
| Тести | `backend/tests/` | Без реальних викликів зовнішніх сервісів |
| Секрети | `~/whisper/backend/.env` **на сервері** | Ніколи не комітити, не друкувати в чат і не писати в документацію |

Репозиторій: `ewgen15/whisper`, гілка `main`. Сервер: SSH `ewgen15@100.67.129.25` (ключ `~/.ssh/id_ed25519`, доступний лише через Tailscale).

## Перевірки (обов'язково перед пушем)

```bash
python3 scripts/check-frontend.py          # синтаксис inline-скриптів у docs/*.html (node --check)
python3 -m py_compile backend/app/*.py
# повний набір — Python 3.11 (локальний python3 = 3.9, `X | None` у модулях на ньому не імпортується):
docker run --rm -v "$PWD/backend:/app" -w /app python:3.11-slim sh -c \
  "pip install -q -r requirements-dev.txt && ruff check . && pytest -q -p no:cacheprovider"
```

- `.githooks/pre-push` запускає швидкі перевірки. Підключення на новій машині: `git config core.hooksPath .githooks`.
- CI: `.github/workflows/ci.yml` (ruff + pytest на 3.11, синтаксис фронтенда, валідність manifest). `deploy-backend.yml` викликає CI першим кроком (`needs: ci`) — червоний CI = немає деплою.
- Додаючи бекенд-логіку, додавай тест у `backend/tests/test_app.py` (зовнішні виклики мокай, див. `test_notion_payload`).
- Після тестів прибери артефакти (`__pycache__`, `.ruff_cache`) — вони в `.gitignore`, але не залишай їх у `backend/`, якщо монтував його в Docker.

## Деплой

- **Фронтенд:** `git push` у `main` → GitHub Pages оновлюється за ~1 хв. Користувач після цього може бачити стару версію через кеш PWA/Safari.
- **Бекенд:** `git push` змін у `backend/**` → GitHub Actions (Tailscale + SSH). **Автодеплой ненадійний** — контейнер може не перебудуватись. Перевіряй і за потреби вручну:
  ```bash
  ssh -i ~/.ssh/id_ed25519 ewgen15@100.67.129.25 \
    'cd ~/whisper && git pull origin main && cd backend && docker compose down && docker compose up -d --build'
  ```
  Використовуй `down` перед `up`, інакше буває «container name conflict / marked for removal».
- Швидко перевірити код у контейнері без деплою: `cat file.py | ssh … 'docker exec -i backend-diary-backend-1 python -'`.

## Змінні середовища бекенду (`backend/.env` на сервері)

| Змінна | Призначення |
|---|---|
| `OPENAI_API_KEY`, `SUPABASE_URL`, `SUPABASE_KEY` | обов'язкові |
| `API_KEY` | якщо задано — усі запити (крім `OPTIONS` і `/health`) вимагають заголовок `X-API-Key`. Користувач вводить його в налаштуваннях застосунку. Бекенд **публічний** (Funnel), тому не вимикай |
| `VECTOR_ENABLED=true` | `/save` додатково пише в `documents` + `embeddings` |
| `NOTION_ENABLED=true`, `NOTION_TOKEN`, (`NOTION_DATABASE_ID`) | `/save` додатково створює сторінку в Notion |

Прапорці за замовчуванням вимкнені. Збій синхронізації не ламає `/save`; статус повертається в полі `sync` відповіді.

## Дані

**Supabase** — проєкт «Vector AI Store» (`qvhndfdgczdtjxwlfsic`, eu-north-1), спільний з n8n:
- `entries` — те, що читає застосунок (`storage.py`).
- `documents` — поля запису як в n8n (`topic`, `type`, `points` = рядок `• a • b`, …).
- `embeddings` — `content` (= `documents.text`), `metadata` (topic/tags/type/mood), `embedding vector(1536)`, `document_id`.
- Модель embedding — **`text-embedding-ada-002`**. Вхідний рядок для вектора збирається в `integrations._embedding_input` у тому самому форматі, що й n8n-вузол («Назва теми: … Теги: … Ключові пункти: …»). **Не змінюй формат**, інакше нові вектори стануть непорівнянними зі старими.

**Notion** — база «🪬 Особистий щоденник» (id `1c745674-5b78-80af-aca8-c828db2feb60`). Властивості: Name, Настрій, Теги, Тип запису, Емоційний стан, Фізичний стан, Час доби, Контекст, Проект, Ім'я контакта. Тіло сторінки — текст + блок «Тези». Select-значення мають збігатися з наявними опціями (список у промпті `enrichment.py`), щоб не плодити нові.

## Промпт розбору (`enrichment.py`)

- Повертає JSON: `title, text, mood, emotional_state, physical_state, context, project, people, tags, entry_type, time_of_day, points`.
- `time_of_day` **рахує код** за київським часом, не модель.
- `project` та `people` — **експериментальні** поля (свідомо без глибокого допрацювання). `people` пишеться лише в Notion «Ім'я контакта».
- Після зміни промпту проганяй реальні приклади (див. «Швидко перевірити» вище): `gpt-4o-mini` схильна копіювати значення з прикладу в промпті та вигадувати ролі («друг») — перевіряй `project`/`context` на записах, де їх немає.

## Підводні камені (вже наступали на граблі)

- **Chrome Private Network Access:** запити з github.io на Tailscale-адреси вимагають `Access-Control-Allow-Private-Network: true`. Це робить чистий ASGI-middleware `_PNAMiddleware` у `main.py`; `BaseHTTPMiddleware` не бачить preflight-відповідей `CORSMiddleware`.
- **iOS PWA має власний `localStorage`**, окремий від Safari: backend URL і API-ключ вводяться в налаштуваннях саме в PWA. Без цього — банер «Бекенд недоступний».
- **iOS ігнорує іконки з manifest** і бере `docs/apple-touch-icon.png`. `icon.svg` — нова біла/`#7B6FA8`; PNG (`apple-touch-icon.png`, `icon-192.png`, `icon-512.png`) **досі старі** (градієнт). Користувач відмовився від генерації PNG скриптами — питай, як оновити.
- Фон-градієнт задано на `html`, висота `.app` — `100dvh`; не повертай фіксований `body::before` (білі смуги на мобільному).
- Банер помилки зсуває шапку через `body.has-banner .hdr`; інакше він перекриває кнопку налаштувань.
- `localStorage['diary_entries']` — кеш-фолбек списку; він маскує проблеми з бекендом, тому для діагностики є панель «Діагностика» в налаштуваннях.
- Авто-режим/класифікатор може блокувати зовнішні тунелі (`tailscale funnel`) та друк секретів — для цього потрібна явна згода користувача. **Не виводь вміст `.env`** (раніше в чат потрапив `OPENAI_API_KEY`).

## Стан робочої копії

- `pwa/`, `test-samples/` у `.gitignore` (локальні). Кореневі `index.html`, `manifest.json`, `prototype.html` позначені як видалені — це давній стан, **не відновлюй і не коміть** їх; жива версія лежить у `docs/`.
- Не вся вміст `design/` потрібен у проді: це демо й референси (`design/references/`). `icon-demo.html`, скріншоти `Saved Messages Image*.jpg`, `.vscode/`, `docs/codemie/` — неtracked, не додавай їх у коміти без прохання.
- Коміти: короткий заголовок англійською, пояснення за потреби; додавай `git add` лише потрібні файли (не `git add -A`).

## Мова

Користувач спілкується українською; UI, промпти й документація — українською. Імена змінних, коментарі в коді та повідомлення комітів — англійською.
