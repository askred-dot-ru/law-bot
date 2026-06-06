#!/usr/bin/env python3
import json, os, logging, asyncio, re
import urllib.request, urllib.error

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

VERSION = "v1-law-bot"
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
API_BASE = "https://api.deepseek.com"
API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
MODEL = "deepseek-chat"

DIR = os.path.dirname(os.path.abspath(__file__))
SYSTEM_PROMPT_PATH = os.path.join(DIR, "system-prompt.md")
DATA_DIR = os.path.join(DIR, "law_bot_data")
DATABASE_URL = os.environ.get("DATABASE_URL", "")

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

import law_search

# ─── DB (PostgreSQL) ──────────────────────────────────────────────

def get_db():
    import psycopg2
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    return conn


def db_init():
    if not DATABASE_URL:
        return
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS law_history (
                id SERIAL PRIMARY KEY,
                chat_id BIGINT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                username TEXT DEFAULT '',
                first_name TEXT DEFAULT '',
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_law_history_chat_id ON law_history(chat_id)")
        for col in ["username TEXT DEFAULT ''", "first_name TEXT DEFAULT ''"]:
            cur.execute(f"ALTER TABLE law_history ADD COLUMN IF NOT EXISTS {col}")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS law_users (
                chat_id BIGINT PRIMARY KEY,
                username TEXT DEFAULT '',
                first_name TEXT DEFAULT '',
                first_seen TIMESTAMPTZ DEFAULT NOW(),
                last_seen TIMESTAMPTZ DEFAULT NOW()
            )
        """)
    conn.close()
    logger.info("PostgreSQL ready")


def db_upsert_user(chat_id, username='', first_name=''):
    if not DATABASE_URL:
        return
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO law_users (chat_id, username, first_name, last_seen)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (chat_id) DO UPDATE SET
                username = EXCLUDED.username,
                first_name = EXCLUDED.first_name,
                last_seen = NOW()
        """, (chat_id, username, first_name))
    conn.close()


def db_load_history(chat_id, system_prompt):
    if not DATABASE_URL:
        return None
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT role, content FROM law_history WHERE chat_id = %s ORDER BY created_at ASC",
            (chat_id,)
        )
        rows = cur.fetchall()
    conn.close()
    messages = [{"role": "system", "content": system_prompt}]
    for role, content in rows:
        messages.append({"role": role, "content": content})
    return messages


def db_save_message(chat_id, role, content, username='', first_name=''):
    if not DATABASE_URL:
        return
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO law_history (chat_id, role, content, username, first_name) VALUES (%s, %s, %s, %s, %s)",
            (chat_id, role, content, username, first_name)
        )
    conn.close()


def db_clear_history(chat_id):
    if not DATABASE_URL:
        return
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM law_history WHERE chat_id = %s", (chat_id,))
    conn.close()


# ─── JSON fallback (local dev) ────────────────────────────────────

def get_history_path(chat_id):
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, f"{chat_id}.json")


def json_load_history(chat_id, system_prompt):
    path = get_history_path(chat_id)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                msgs = json.load(f)
            msgs[0]["content"] = system_prompt
            return msgs
        except Exception:
            pass
    return None


def json_save_history(chat_id, messages):
    path = get_history_path(chat_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(messages, f, ensure_ascii=False, indent=2)


def json_clear_history(chat_id):
    path = get_history_path(chat_id)
    if os.path.exists(path):
        os.remove(path)


# ─── Storage abstraction ──────────────────────────────────────────

def load_system_prompt():
    with open(SYSTEM_PROMPT_PATH, "r", encoding="utf-8") as f:
        return f.read()


def load_history(chat_id, system_prompt):
    if DATABASE_URL:
        return db_load_history(chat_id, system_prompt)
    result = json_load_history(chat_id, system_prompt)
    if result:
        return result
    return [{"role": "system", "content": system_prompt}]


def save_message(chat_id, role, content, username='', first_name=''):
    if DATABASE_URL:
        db_save_message(chat_id, role, content, username, first_name)
    else:
        path = get_history_path(chat_id)
        messages = json_load_history(chat_id, "")
        if messages is None:
            messages = [{"role": "system", "content": load_system_prompt()}]
        messages.append({"role": role, "content": content})
        json_save_history(chat_id, messages)


def clear_history(chat_id):
    if DATABASE_URL:
        db_clear_history(chat_id)
    else:
        json_clear_history(chat_id)


# ─── Formatting ──────────────────────────────────────────────────

def format_telegram(text):
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    return text


# ─── Law search integration ──────────────────────────────────────

def build_law_context(user_text: str) -> str:
    try:
        results = law_search.search_law(user_text, top_k=5)
    except Exception as e:
        logger.error(f"Law search error: {e}")
        return ""

    if not results:
        return ""

    lines = ["\n\nНиже приведены релевантные статьи из кодексов РФ. Используй их для ответа:\n"]
    seen = set()
    for r in results:
        key = f"{r['codex']}_{r['article']}"
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"--- {r['codex']} | {r['article']} ---")
        lines.append(r["text"])
    return "\n".join(lines)


# ─── API ──────────────────────────────────────────────────────────

def call_chat(messages):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}",
    }
    data = json.dumps({
        "model": MODEL, "messages": messages,
        "temperature": 0.7, "max_tokens": 4096,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE}/chat/completions", data=data, headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        return result["choices"][0]["message"]["content"]


# ─── Handlers ─────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    db_upsert_user(chat_id, user.username or '', user.first_name or '')
    clear_history(chat_id)
    save_message(chat_id, "system", load_system_prompt())
    await update.message.reply_text(
        "⚖️ Добро пожаловать в LawBot — юридический консультант по законодательству РФ!\n\n"
        "Я помогаю разобраться в Гражданском, Уголовном, Семейном и Налоговом кодексах.\n\n"
        "Команды:\n"
        "/clear — начать диалог заново\n"
        "/help — справка\n"
        "/codexes — список доступных кодексов\n"
        "/article ГК 1 — текст конкретной статьи\n\n"
        "Задавай любой юридический вопрос!"
    )


async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    clear_history(chat_id)
    save_message(chat_id, "system", load_system_prompt())
    await update.message.reply_text("✅ История диалога очищена.")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚖️ LawBot — юридический консультант по законодательству РФ.\n\n"
        "Я анализирую Гражданский, Уголовный, Семейный и Налоговый кодексы "
        "и нахожу релевантные статьи под ваш вопрос.\n\n"
        "Команды:\n"
        "/start — приветствие\n"
        "/clear — очистить историю\n"
        "/codexes — список кодексов\n"
        "/article ГК 158 — текст статьи\n"
        "/help — эта справка\n\n"
        "Просто напиши вопрос — я найду нужные статьи и отвечу."
    )


async def codexes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        result = law_search.list_codexes()
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")
        return

    if not result:
        await update.message.reply_text("Кодексы не найдены.")
        return

    lines = ["📚 Доступные кодексы:\n"]
    for c in result:
        lines.append(f"• {c['codex']} — {c['article_count']} статей")
    await update.message.reply_text("\n".join(lines))


async def article_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = update.message.text.strip().split()
    if len(args) < 2:
        await update.message.reply_text(
            "Укажите кодекс и номер статьи. Примеры:\n"
            "/article ГК 1\n"
            "/article УК 158\n"
            "/article СК 12\n"
            "/article НК 20"
        )
        return

    codex = args[1]
    article_num = "Статья " + " ".join(args[2:]) if len(args) > 2 else "Статья"

    try:
        result = law_search.get_article(codex, article_num)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")
        return

    if result is None:
        await update.message.reply_text(f"❌ Статья не найдена: {article_num} ({codex})")
        return

    text = result["text"]
    if len(text) > 4000:
        text = text[:4000] + "\n\n... (текст сокращён)"

    await update.message.reply_text(
        f"<b>{result['codex']}</b>\n<b>{result['article']}</b>\n\n{text}",
        parse_mode="HTML"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    username = user.username or ''
    first_name = user.first_name or ''
    user_text = update.message.text.strip()
    if not user_text:
        return

    db_upsert_user(chat_id, username, first_name)

    law_context = build_law_context(user_text)

    system_prompt = load_system_prompt()
    if law_context:
        system_prompt = system_prompt + law_context

    messages = load_history(chat_id, system_prompt)
    messages.append({"role": "user", "content": user_text})

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        response = await asyncio.to_thread(call_chat, messages)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        msg = f"❌ Ошибка API: {body}"
        await update.message.reply_text(msg)
        return
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")
        return

    save_message(chat_id, "user", user_text, username, first_name)
    save_message(chat_id, "assistant", response)

    await update.message.reply_text(format_telegram(response), parse_mode="HTML")


def main():
    if DATABASE_URL:
        db_init()
    else:
        os.makedirs(DATA_DIR, exist_ok=True)

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", clear_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("codexes", codexes_cmd))
    app.add_handler(CommandHandler("article", article_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    is_railway = "RAILWAY_ENVIRONMENT" in os.environ or "RAILWAY_SERVICE_NAME" in os.environ
    port = int(os.environ.get("PORT", "8080"))
    public_url = os.environ.get("PUBLIC_URL", "")

    if is_railway and public_url:
        logger.info(f"Railway mode: webhook on port {port}, url={public_url}")
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=BOT_TOKEN,
            webhook_url=f"{public_url}/{BOT_TOKEN}",
            allowed_updates=Update.ALL_TYPES,
        )
    else:
        logger.info("Local mode: polling")
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
