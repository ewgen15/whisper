# Whisper — голосовий щоденник (інструкція для агентів)

Прочитай цей файл першим. Деталі: `ARCHITECTURE.md` (чому так побудовано), `PLAN.md` (статус і roadmap), `SCREENS.md` (UX-специфікація екранів).

## Що це

PWA українською: користувач диктує думку → Whisper транскрибує → GPT розбирає на поля → запис зберігається в Supabase (і, коли ввімкнено, у Notion + вектор для семантичного пошуку). Проєкт поступово **замінює n8n-флоу** щоденника.

**Це мобільний застосунок** (iPhone, PWA з Home Screen). Будь-яку UI-зміну оцінюй на малому екрані та з `env(safe-area-inset-*)`.

## Два джерела записів

Записи надходять двома незалежними шляхами в **ті самі** таблиці Supabase та базу Notion:
1. **n8n** (Telegram-бот → n8n → Notion + `documents`/`embeddings`) — основний шлях користувача.
2. **Цей застосунок** (`/save`) — поступово замінює n8n.

Вони не перетинаються: застосунок не чіпає n8n, n8n не знає про застосунок, тож дублів не буде, якщо не диктувати одну думку в обидва місця. Записи не позначені джерелом (колонки/властивості «Джерело» немає). Telegram-вхід у цей бекенд не перенесений: для повної заміни n8n потрібен webhook, що бере голосове з бота й викликає `transcribe → enrich → save`. Щоб зберегти порівнянність даних, формат рядка для embedding і значення Notion-селектів мають лишатися такими, як в n8n.

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
      POST /save         entries (Supabase) + sync: documents/embeddings + Notion; зберігає notion_page_id/document_id
      GET  /entries      читання з Supabase
      PATCH  /entries/id редагування (title,text,tags,mood,…) + оновлення Notion-сторінки та embedding
      DELETE /entries/id видалення; Notion-сторінка архівується, documents/embeddings видаляються
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

- **Версія фронтенда:** після будь-якої зміни `docs/index.html` запускай `python3 scripts/stamp-version.py` — він пише `APP_VERSION` (дата + хеш вмісту), що видно під лого «Diary» і в Діагностиці. Pre-push і CI (`--check`) падають, якщо штамп застарів.
- `.githooks/pre-push` запускає швидкі перевірки. Підключення на новій машині: `git config core.hooksPath .githooks`.
- CI: `.github/workflows/ci.yml` (ruff + pytest на 3.11, синтаксис фронтенда, валідність manifest). `deploy-backend.yml` викликає CI першим кроком (`needs: ci`) — червоний CI = немає деплою.
- Додаючи бекенд-логіку, додавай тест у `backend/tests/` (зовнішні виклики мокай — фікстури `sb`, `oai`, `notion` у `conftest.py`).
- Тести покривають: авторизацію й CORS/PNA; формат embedding-рядка; `save_vector` (порядок `documents` → `embeddings`, модель ada-002, пропуск порожнього тексту); payload Notion; ізоляцію збоїв у `sync_entry`; `/save` і поле `sync`; постобробку `enrich` (час доби рахує код). Також: update/delete у Notion і векторах, PATCH/DELETE ендпоїнти, rate limit, структура CI-воркфлоу (`test_repo.py`). **Не покрито:** реальні виклики OpenAI/Supabase/Notion (перевіряються вручну тестовим записом, який потім видаляється), якість самого промпту, поведінку UI.
- Після тестів прибери артефакти (`__pycache__`, `.ruff_cache`) — вони в `.gitignore`, але не залишай їх у `backend/`, якщо монтував його в Docker.

## Деплой

- **Фронтенд:** `git push` у `main` → GitHub Pages оновлюється за ~1 хв. Користувач після цього може бачити стару версію через кеш PWA/Safari.
- **Бекенд:** `git push` змін у `backend/**` → GitHub Actions (Tailscale + SSH). **Автодеплой ненадійний** — контейнер може не перебудуватись. Перевіряй і за потреби вручну:
  ```bash
  ssh -i ~/.ssh/id_ed25519 ewgen15@100.67.129.25 \
    'cd ~/whisper && git pull origin main && cd backend && docker compose down && docker compose up -d --build'
  ```
  Використовуй `down` перед `up`, інакше буває «container name conflict / marked for removal». Те саме робить `scripts/deploy.sh`.
- **CI-джоби:** `backend`, `frontend`, `docker` (збірка образу + `/health`). Вони мають бути *required checks* у branch protection на `main` (налаштовується в GitHub UI).
- **Алерт:** `.github/workflows/uptime.yml` кожні 15 хв б'є в `/health`; якщо сервер лежить — відкриває issue `server-down` і падає (GitHub надсилає email), коли оживе — закриває issue.
- **Rate limit:** `ratelimit.py` + `_GuardMiddleware` у `main.py` (ключ і ліміти в одному місці). Правильний ключ: 120 запитів/хв (`RATE_LIMIT_PER_MIN`); неправильний ключ: 10 спроб/хв на клієнта (`FAILED_AUTH_PER_MIN`) окремим лічильником, щоб флуд сторонніх не вичерпав ліміт власника (за Funnel усі можуть мати одну адресу). `/health` і OPTIONS не лімітуються; 401/429 мають CORS-заголовки (порядок: PNA → CORS → Guard).
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
- Фон-градієнт на `body`, `html, body { height:100% }`, `.app { height:100vh }`. В iOS PWA layout viewport буває коротшим за екран (793 проти 852 на iPhone з островом) — це давало смужку знизу; її лікує `fitScreenHeight()` в `index.html` (тягне html/body/.app до `screen.height`, коли розрив 20–120pt). Не повертай `100dvh`, фіксований `::before` чи фон на корені — пробували, не працює.
- Банер помилки зсуває шапку через `body.has-banner .hdr`; інакше він перекриває кнопку налаштувань.
- `localStorage['diary_entries']` — кеш-фолбек списку; він маскує проблеми з бекендом, тому для діагностики є панель «Діагностика» в налаштуваннях.
- Авто-режим/класифікатор може блокувати зовнішні тунелі (`tailscale funnel`) та друк секретів — для цього потрібна явна згода користувача. **Не виводь вміст `.env`** (раніше в чат потрапив `OPENAI_API_KEY`).

## Стан робочої копії

- `pwa/`, `test-samples/` у `.gitignore` (локальні). Кореневі `index.html`, `manifest.json`, `prototype.html` позначені як видалені — це давній стан, **не відновлюй і не коміть** їх; жива версія лежить у `docs/`.
- Не вся вміст `design/` потрібен у проді: це демо й референси (`design/references/`). `icon-demo.html`, скріншоти `Saved Messages Image*.jpg`, `.vscode/`, `docs/codemie/` — неtracked, не додавай їх у коміти без прохання.
- Коміти: короткий заголовок англійською, пояснення за потреби; додавай `git add` лише потрібні файли (не `git add -A`).

## Мова

Користувач спілкується українською; UI, промпти й документація — українською. Імена змінних, коментарі в коді та повідомлення комітів — англійською.
