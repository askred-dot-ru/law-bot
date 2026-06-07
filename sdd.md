# Software Design Document: LawBot

## 1. Введение

### 1.1 Назначение
**LawBot** — Telegram-бот, консультирующий пользователей по энергетическому законодательству РФ. Релевантные нормы извлекаются через MCP-сервер векторного поиска из базы 49 ключевых документов (Конституция, 18 кодексов, 5 ФЗ, 14 постановлений Правительства, 1 распоряжение, 10 приказов) и подаются в контекст LLM.

### 1.2 Стек
| Компонент | Технология |
|-----------|------------|
| Язык | Python 3.11 |
| Telegram API | `python-telegram-bot[webhooks]` 22.x |
| LLM | DeepSeek `deepseek-chat` |
| MCP SDK | `fastmcp` (Python) |
| Векторная БД | ChromaDB |
| Эмбеддинги | `intfloat/multilingual-e5-small` |
| База истории | PostgreSQL (Railway) |
| Деплой | Railway Hobby (один совмещённый сервис) |
| План Railway | Hobby ($5/мес, $5 кредита на ресурсы) |
| RAM | До 48 GB (хватит для локальной модели 470MB) |

### 1.3 Именование
Все файлы и папки проекта именуются в нижнем регистре латиницей, слова разделяются дефисом. Примеры: `law-bot`, `mcp-server.py`, `system-prompt.md`.

---

## 2. Архитектура

### 2.1 Схема компонентов

```
                        ┌──────────────────────────────────────────────┐
                        │          Railway (один сервис)                │
                        │                                              │
   ┌───────────┐       │   ┌──────────────┐     ┌──────────────────┐   │
   │ Telegram  │  webhook  │  bot.py      │ MCP │  mcp-server.py   │   │
   │           │◄────────►│              │◄───►│                  │   │
   │           │  :8080   │ (webhook)    │local│  (FastMCP HTTP)  │   │
   └───────────┘       │   │              │host │  :8090          │   │
                        │   └──────┬───────┘     └────────┬─────────┘   │
                        │          │                      │             │
                        │          ▼                      ▼             │
                        │   ┌──────────────┐     ┌──────────────┐       │
                        │   │ PostgreSQL   │     │  ChromaDB    │       │
                        │   │ (история,    │     │  (векторы)   │       │
                        │   │  пользоват.) │     │  в /app/db/  │       │
                        │   └──────────────┘     │              │       │
                        │                        │  ┌─────────┐ │       │
                        │                        │  │ model   │ │       │
                        │                        │  │ (470MB) │ │       │
                        │                        │  └─────────┘ │       │
                        │                        └──────────────┘       │
                        │          ▲                      │             │
                        └──────────┼──────────────────────┼─────────────┘
                                   │                      │
                              ┌────┴─────┐          ┌─────┴──────┐
                              │ DeepSeek │          │ внешние    │
                              │ API      │          │ MCP-клиенты│
                              └──────────┘          └────────────┘
```

### 2.2 Поток данных (обработка одного сообщения)

```
1. Telegram  ──► webhook POST /<BOT_TOKEN>
2. bot.py    ──► извлекает chat_id, user, текст
3. bot.py    ──► HTTP POST localhost:8090/mcp  {"tool":"search_law", "query":"<вопрос>"}
4. mcp-server──► model.encode(query) → ChromaDB.similarity_search(vector, k=5)
5. mcp-server──► возвращает JSON: [{article, codex, text, score}, ...]
6. bot.py    ──► формирует промпт:
                  system: "Ты юрист. Отвечай по этим статьям: ..."
                  history: [предыдущие сообщения]
                  user: "<вопрос пользователя>"
7. bot.py    ──► POST https://api.deepseek.com/chat/completions
8. bot.py    ──► форматирует ответ (HTML), отправляет в Telegram
9. bot.py    ──► сохраняет user+assistant в PostgreSQL
```

### 2.3 Временной бюджет (target)
| Шаг | Время |
|-----|-------|
| MCP-поиск (ChromaDB) | <50ms |
| DeepSeek API | 1-5s |
| Форматирование + отправка | <100ms |
| **Итого** | **2-6s** |

---

## 3. Детальный дизайн компонентов

### 3.1 `mcp-server.py` — MCP-сервер векторного поиска

**Фреймворк:** FastMCP (реализует MCP-протокол через HTTP/SSE)

**Конфигурация:**
```
MCP_PORT=8090
CHROMA_PATH=/app/db/chroma
COLLECTION_NAME=law_codes
```

**Инструменты (tools):**

| Имя | Параметры | Возврат |
|-----|-----------|---------|
| `search_law` | `query: str`, `top_k: int = 5` | `[{codex, article, section, text, score}]` |
| `get_article` | `codex: str`, `article: str` | `{codex, article, text}` |
| `list_codexes` | — | `[{codex, description, article_count}]` |

**Ресурсы (resources):**
- `law://{codex}/{article}` — текст конкретной статьи

**Инициализация:**
```python
# При старте:
# 1. Загружает модель sentence-transformers (470MB) один раз
# 2. Загружает ChromaDB из /app/db/chroma
# 3. При поиске: model.encode(query) → вектор → ChromaDB.query()
# Модель НЕ выгружается между запросами — висит в RAM
```

### 3.2 `ingest.py` — скрипт индексации

**Запуск:** однократно при сборке Docker-образа (не при старте сервиса)

**Алгоритм:**
1. Читает `output.md`
2. Разбивает на секции по `# ` заголовкам → определяет кодекс
3. Внутри секции разбивает на чанки: 300-400 символов, перекрытие 50
4. Для каждого чанка извлекает метаданные: `{codex, article, section, chunk_index}`
5. Скачивает и загружает модель `intfloat/multilingual-e5-small` (470MB)
6. Эмбеддит чанки локально через модель (батчами по 32, ~15-20 мин на CPU)
7. Сохраняет в ChromaDB (`/app/db/chroma`)

**Метаданные чанка:**
```json
{
  "codex": "Гражданский кодекс РФ",
  "article": "Статья 1",
  "section": "Раздел I. Общие положения / Глава 1",
  "chunk_index": 0,
  "char_count": 387
}
```

### 3.3 `bot.py` — Telegram-бот

**Архитектура:** повторяет `benpan-bot.py`, ключевые отличия в `# ─── MCP integration ───`

```
benpan-bot.py:
  messages = [system_prompt, ...history, user_text]
  → DeepSeek

law-bot.py:
  context = mcp_search(user_text)          # ← НОВОЕ
  messages = [system_prompt + context, ...history, user_text]
  → DeepSeek
```

**Класс для MCP-клиента:**
```python
class McpClient:
    def __init__(self, base_url="http://localhost:8090"):
        self.base_url = base_url
    
    def search_law(self, query: str, top_k: int = 5) -> list[dict]:
        # HTTP POST к MCP-серверу, вызов инструмента search_law
        ...
    
    def get_article(self, codex: str, article: str) -> dict:
        ...
    
    def list_codexes(self) -> list[dict]:
        ...
```

**Системный промпт (`system_prompt.md`):**
```
Ты — юрист-консультант. Тебя зовут Алексей.
Твоя задача — отвечать на вопросы пользователей по законодательству РФ.
В каждом запросе тебе будут предоставлены релевантные статьи из кодексов.

Правила:
- Отвечай строго на основе предоставленных статей
- Всегда указывай номера статей и названия кодексов
- Если информации недостаточно — честно скажи об этом
- Отвечай понятным языком, без юридического жаргона
- Обращайся на «Вы»

Доступные кодексы:
- Гражданский кодекс РФ (ГК РФ)
- Уголовный кодекс РФ (УК РФ)
- Семейный кодекс РФ (СК РФ)
- Налоговый кодекс РФ (НК РФ)
```

**Обработчики команд:**

| Команда | Описание |
|---------|----------|
| `/start` | Приветствие, очистка истории |
| `/clear` | Очистка диалога |
| `/help` | Справка |
| `/codexes` | Список доступных кодексов (через MCP `list_codexes`) |
| `/article <кодекс> <номер>` | Точная выдача статьи (через MCP `get_article`) |

**Форматирование ответа:**
- Markdown → HTML (как в StepanBot)
- Номера статей в ответе выделяются **жирным**
- Кодексы — курсивом

### 3.4 Хранение истории

Полный аналог StepanBot:
- **Production** (Railway): PostgreSQL — таблицы `bot_history`, `bot_users`
- **Development** (локально): JSON-файлы в `data/`

---

## 4. Docker-образ (совмещённый)

### 4.1 Multi-stage Dockerfile

```dockerfile
# Stage 1: builder — индексация векторов
FROM python:3.11-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Предзагрузка модели в кэш (до запуска ingest.py)
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('intfloat/multilingual-e5-small')"
COPY output.md ingest.py ./
RUN python ingest.py  # → /app/db/chroma/

# Stage 2: runtime — MCP + бот
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=builder /app/db/ /app/db/
# Копируем закэшированную модель из builder-стадии
COPY --from=builder /root/.cache/torch/sentence_transformers/ /root/.cache/torch/sentence_transformers/
COPY mcp-server.py bot.py system-prompt.md entrypoint.sh ./
RUN chmod +x entrypoint.sh

EXPOSE 8080 8090
CMD ["./entrypoint.sh"]
```

### 4.2 `entrypoint.sh`

```bash
#!/bin/bash
set -e

# Запуск MCP-сервера в фоне
python mcp-server.py --port 8090 --chroma-path /app/db/chroma &
MCP_PID=$!
sleep 2  # дать MCP подняться

# Запуск Telegram-бота (основной процесс)
python bot.py

# Если бот упал — гасим MCP
kill $MCP_PID 2>/dev/null
```

---

## 5. Деплой

### 5.1 Модель деплоя

Деплой идёт через **GitHub → Railway**. При каждом `git push` в ветку `main` Railway триггерит билд Docker-образа и деплой.

```
git push origin main  ──►  Railway webhook  ──►  Docker build (2 стадии)
                                                     │
                                              Stage 1: builder
                                              pip install → ingest.py → ChromaDB index
                                                     │
                                              Stage 2: runtime
                                              копирует /app/db/ из builder → entrypoint.sh
```

### 5.2 GitHub-репозиторий

**URL**: `https://github.com/askred-dot-ru/law-bot`

**Структура в репо**:
```
/                          # корень репо (s/)
├── output.md              # 189k строк, 50 MB — датасет
├── fetch_codes.py         # загрузка кодексов (API pravo.gov.ru)
├── fetch_all.py           # загрузка ФЗ/ПП/приказов (consultant.ru)
├── fetch_constitution.py
├── fetch_documents.py
├── sdd.md                 # этот документ
│
└── law-bot/               # исходники бота
    ├── Dockerfile
    ├── bot.py             # Telegram-бот (python-telegram-bot)
    ├── mcp-server.py      # MCP-сервер поиска (fastmcp)
    ├── law_search.py      # поиск по ChromaDB (sentence-transformers)
    ├── ingest.py          # индексация output.md → ChromaDB
    ├── system-prompt.md   # системный промпт для LLM
    ├── entrypoint.sh      # точка входа в контейнер
    ├── requirements.txt
    └── deploy.sh          # альтернативный деплой через railway up
```

### 5.3 Railway-проект

| Параметр | Значение |
|----------|----------|
| Workspace | askred-dot-ru's Projects |
| Project | law-bot |
| Plan | Hobby ($5/мес) |
| GitHub repo | askred-dot-ru/law-bot |
| Root Directory | `law-bot/` (Railway билдит из подпапки) |
| Deploy trigger | Push to `main` |

### 5.4 Переменные окружения (Railway Dashboard)

| Переменная | Значение | Описание |
|-----------|----------|----------|
| `BOT_TOKEN` | `8704425426:AAFMwHrl-ricK4JmPib4r-pj-gO3hdZJRpI` | Токен Telegram |
| `DEEPSEEK_API_KEY` | `sk-afa1c5...` | API-ключ DeepSeek |
| `DATABASE_URL` | авто (Railway) | PostgreSQL |
| `PUBLIC_URL` | `https://law-bot-....up.railway.app` | Webhook URL |
| `PORT` | `8080` | Порт webhook |
| `MCP_PORT` | `8090` | Порт MCP-сервера |

### 5.5 Индексация (Docker Build Stage 1)

**`Dockerfile` — две стадии**:
```dockerfile
# Stage 1: builder — индексация векторов
FROM python:3.11-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY output.md ingest.py ./
RUN python ingest.py

# Stage 2: runtime — MCP + bot
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=builder /app/db/ /app/db/
COPY --from=builder /root/.cache/huggingface/ /root/.cache/huggingface/
COPY mcp-server.py bot.py law_search.py system-prompt.md entrypoint.sh ./
RUN chmod +x entrypoint.sh
EXPOSE 8080 8090
CMD ["./entrypoint.sh"]
```

**Процесс индексации** (`ingest.py`):
1. Читает `output.md` → разбивает на секции по `# `-заголовкам
2. Внутри каждой секции находит статьи по regex `Статья \d+(?:\.\d+)?`
3. Режет статьи на чанки по 400 символов с перекрытием 50
4. Модель `intfloat/multilingual-e5-small` эмбеддит каждый чанк
5. Сохраняет в ChromaDB (`/app/db/chroma`)

**Метаданные каждого чанка**:
- `codex` — название документа (напр. «Гражданский кодекс РФ», «Постановление Правительства РФ от ...»)
- `article` — номер статьи/пункта
- `section` — раздел/глава
- `chunk_index` — номер чанка внутри статьи

### 5.6 Альтернативный деплой: `deploy.sh`

Для деплоя без GitHub (через `railway up`):

```bash
#!/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEPLOY_DIR="/tmp/law-bot-deploy"

rm -rf "$DEPLOY_DIR"
mkdir -p "$DEPLOY_DIR"

cp "$SCRIPT_DIR/bot.py" "$DEPLOY_DIR/"
cp "$SCRIPT_DIR/mcp-server.py" "$DEPLOY_DIR/"
cp "$SCRIPT_DIR/law_search.py" "$DEPLOY_DIR/"
cp "$SCRIPT_DIR/ingest.py" "$DEPLOY_DIR/"
cp "$SCRIPT_DIR/system-prompt.md" "$DEPLOY_DIR/"
cp "$SCRIPT_DIR/requirements.txt" "$DEPLOY_DIR/"
cp "$SCRIPT_DIR/Dockerfile" "$DEPLOY_DIR/"
cp "$SCRIPT_DIR/entrypoint.sh" "$DEPLOY_DIR/"
cp "$SCRIPT_DIR/../output.md" "$DEPLOY_DIR/"

export PATH="$HOME/.npm-global/bin:$PATH"
export RAILWAY_API_TOKEN="a5bea384-f730-4ce1-8eee-94ab05204338"

cd "$DEPLOY_DIR" && railway up --detach --service law-bot
```

### 5.7 Чеклист деплоя

При каждом обновлении:
- [ ] `output.md` обновлён (fetch_codes.py / fetch_all.py)
- [ ] `system-prompt.md` актуален
- [ ] `sdd.md` отражает изменения
- [ ] `git add -A && git commit -m "..." && git push origin main`
- [ ] Проверить Railway Dashboard → Deployments → статус сборки
- [ ] После деплоя: `/start` в Telegram-боте → проверка ответа
- [ ] Проверить логи: Railway → law-bot → Deploy Logs

---

## 6. Ограничения и риски

| Риск | Влияние | Митигация |
|------|---------|-----------|
| `output.md` 23.8MB — долгая сборка образа | Медленный первый деплой | Индексация только в builder-стадии, не на старте |
| ChromaDB в Docker — данные теряются при перезапуске | Потеря индекса | Индекс вшит в образ при сборке |
| Локальная модель 470MB увеличивает холодный старт | Долгий первый запуск (~5 сек) | Модель загружается один раз и кэшируется в памяти |
| RAM ~820MB — не влезает на Free-план | Невозможность деплоя на Free | Используем Hobby (48GB лимит), запас 50x |
| Multilingual-e5-small не идеален для юр. текста | Качество поиска | Можно заменить на `dunzhang/stella_en_400M_v5` или OpenAI embeddings |
| DeepSeek может галлюцинировать по юр. вопросам | Неверные консультации | Системный промпт жёстко требует опираться на предоставленные статьи |
| Один сервис = одна точка отказа | Если упал MCP — бот не работает | Автоперезапуск Railway + healthcheck |

---

## 7. Ресурсы на Railway Hobby

### 7.1 Потребление RAM (рантайм)

| Компонент | RAM |
|-----------|-----|
| Python + бот | ~100 MB |
| ChromaDB (30k векторов × 384d) | ~200 MB |
| sentence-transformers модель | ~500 MB |
| PostgreSQL-драйвер | ~20 MB |
| **Итого** | **~820 MB** |

Лимит Hobby: 48 GB → запас 50x.

### 7.2 Стоимость

| Ресурс | Потребление | Цена | В месяц |
|--------|-------------|------|---------|
| RAM: 0.82 GB × 24/7 | 0.82 GB-мес | $10/GB | ~$8.20 |
| CPU: 1 vCPU (низкая нагрузка) | ~0.3 vCPU-мес | $20/vCPU | ~$6 |
| DeepSeek API (внешний) | ~1000 запросов | — | ~$2 |
| **Итого ресурсы** | | | **~$16** |
| Кредит Hobby | | | **-$5** |
| Подписка Hobby | | | **$5** |
| **Факт. счёт** | | | **~$16** |
