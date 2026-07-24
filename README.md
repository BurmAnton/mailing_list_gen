# MailCraft — персонализированная генерация рассылок (RunPod AI)

Веб-приложение: импорт получателей → анализ компании (поля CSV + опционально сайт) → генерация темы и текста через RunPod → ревью/правка → экспорт. Задел под Яндекс.Почту и сервисы рассылок.

## Стек

- Backend: FastAPI + SQLAlchemy (SQLite) + httpx
- Frontend: React + Vite + TypeScript
- LLM: RunPod Serverless (OpenAI-compatible `/v1/chat/completions`)

## LLM: OpenRouter (по умолчанию) + RunPod

По умолчанию используется **OpenRouter**. В UI загружаются модели для **поиска/RAG** с ценами
(`$prompt / $completion за 1M tok`), отсортированные по цене.

Форма кампании:
- Excel/CSV: обязателен только `email`, остальные колонки — контекст письма
- RAG-файлы (pdf/docx/txt/md/csv) — общие для всех писем, сжимаются в один digest
- Веб-поиск — **opt-in**, по умолчанию выключен
- Перед генерацией показывается оценка стоимости

В `.env`:

```bash
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_DEFAULT_MODEL=perplexity/sonar-pro
OPENROUTER_WEB_PLUGIN=true
```

Эндпоинты: `GET /api/models?capability=search_rag`, `POST /api/campaigns/{id}/rag`, `GET /api/campaigns/{id}/cost-estimate`

## Быстрый старт

### 1. Конфиг

```bash
cp .env.example .env
```

Заполните в `.env`:

- `RUNPOD_API_KEY` — API-ключ RunPod
- `RUNPOD_ENDPOINT_URL` — URL OpenAI-compatible endpoint, например  
  `https://api.runpod.ai/v2/<ENDPOINT_ID>/openai/v1`
- `RUNPOD_MODEL` — имя модели на эндпоинте

### 2. Backend

```bash
cd backend
python -m venv .venv
# Windows:
.\.venv\Scripts\activate
# macOS/Linux:
# source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --app-dir . --port 8000
```

Или из каталога `backend`:

```bash
uvicorn app.main:app --reload --port 8000
```

API: http://localhost:8000  
Docs: http://localhost:8000/docs

### 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

UI: http://localhost:5173

## Формат файла получателей

Обязательные колонки: `email`, `company_name`.

Опционально: `name`, `industry`, `description`, `website_url`.

Пример: [`samples/recipients.csv`](samples/recipients.csv)

## Поток работы

1. Создать кампанию и задать базовый промпт.
2. Импортировать CSV/XLSX.
3. Включить при необходимости «Анализировать сайты».
4. Запустить генерацию (прогресс обновляется автоматически).
5. Отредактировать черновики или перегенерировать отдельные письма.
6. Экспортировать CSV/JSON.

## Будущие интеграции

Абстракция `MailProvider` уже есть:

- `yandex_smtp` — Яндекс.Почта через SMTP (`smtp.yandex.ru`, пароль приложения)
- `mailing_service` — UniSender / SendPulse / аналоги через API

В MVP отправка отключена; эндпоинт `POST /api/providers/{type}/send` возвращает сообщение-заглушку. Список провайдеров: `GET /api/providers`.

### Как подключить позже

**Яндекс SMTP:** добавить в `.env` логин и app-password, реализовать `YandexSmtpProvider.send_batch` через `aiosmtplib`.

**Сервис рассылок:** хранить API-ключ в `ProviderConfig.credentials_json`, вызвать REST API провайдера в `MailingServiceProvider.send_batch`.

## Основные API

| Метод | Путь | Описание |
|-------|------|----------|
| POST | `/api/campaigns` | Создать кампанию |
| GET | `/api/campaigns/{id}` | Детали |
| POST | `/api/campaigns/{id}/recipients/import` | Импорт файла |
| POST | `/api/campaigns/{id}/generate` | `{ "enrich_websites": true }` |
| GET | `/api/campaigns/{id}/status` | Прогресс |
| GET/PATCH | `/api/drafts/{id}` | Черновик |
| POST | `/api/drafts/{id}/regenerate` | Перегенерация |
| GET | `/api/campaigns/{id}/export?format=csv\|json` | Экспорт |

## Структура

```
backend/app/
  api/           # REST
  models/        # SQLAlchemy
  schemas/       # Pydantic
  services/      # RunPod, enrichment, generation, import
  providers/     # MailProvider stubs
frontend/src/
  pages/         # UI кампаний и ревью
samples/         # пример CSV
```
