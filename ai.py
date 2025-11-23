import logging
import html
import asyncio
import requests
import json
import threading
from typing import List, Tuple, Dict, Any
from pathlib import Path

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
)
from telegram.constants import ParseMode, ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes
)

# === Конфигурация ===
TELEGRAM_TOKEN = "8115570934:AAGFJFnNDo5lxDlE3XZEQ_W63WjuoYYHJJM"
OPENROUTER_API_KEY = "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"
BOT_NAME = "Gugapiti"

DEFAULT_MODEL = "openai/gpt-5-pro"
DEFAULT_CODE_STYLE = "auto"
TYPING_PULSE_SEC = 2

# state file
STATE_FILE = Path("bot_state.json")
STATE_LOCK = threading.Lock()

# === Логи ===
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
log = logging.getLogger(BOT_NAME)

# === Хранилища (по-умолчанию, будут загружены из JSON) ===
user_sessions: Dict[str, List[Dict[str, str]]] = {}
user_models: Dict[str, str] = {}
user_styles: Dict[str, str] = {}
user_stickers: Dict[str, str] = {}  # sticker_id per user
meta: Dict[str, Any] = {}  # future metadata

# === Дефолтные стикеры (можешь заменить на свои) ===
DEFAULT_STICKERS = {
    "greeting": "CAACAgIAAxkBAAEBQJ1g1uQk2a1f8wq-EXAMPLE_STICKER_1",  # замените на рабочие id
    "newchat": "CAACAgIAAxkBAAEBQJ5g1uQk2a1f8wq-EXAMPLE_STICKER_2",
    "clear": "CAACAgIAAxkBAAEBQJ9g1uQk2a1f8wq-EXAMPLE_STICKER_3",
    "error": "CAACAgIAAxkBAAEBQJ_g1uQk2a1f8wq-EXAMPLE_STICKER_4",
    "reply": "CAACAgIAAxkBAAEBQKBg1uQk2a1f8wq-EXAMPLE_STICKER_5",
}

# === Вспомогательные функции сохранения/загрузки состояния ===

def _read_state_file() -> Dict[str, Any]:
    """Читает JSON файл состояния (без блокировки)."""
    try:
        if not STATE_FILE.exists():
            return {}
        with STATE_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
            return data
    except Exception as e:
        log.error(f"Ошибка чтения состояния: {e}")
        return {}

def _write_state_file(data: Dict[str, Any]) -> None:
    """Пишет JSON файл состояния (без блокировки)."""
    try:
        tmp = STATE_FILE.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(STATE_FILE)
    except Exception as e:
        log.error(f"Ошибка записи состояния: {e}")

def load_state() -> None:
    """Загружает состояние из файла в глобальные структуры."""
    global user_sessions, user_models, user_styles, user_stickers, meta
    with STATE_LOCK:
        data = _read_state_file()
        user_sessions = {k: v for k, v in data.get("user_sessions", {}).items()}
        user_models = {k: v for k, v in data.get("user_models", {}).items()}
        user_styles = {k: v for k, v in data.get("user_styles", {}).items()}
        user_stickers = {k: v for k, v in data.get("user_stickers", {}).items()}
        meta = data.get("meta", {})
    log.info("State loaded.")

def save_state_async() -> None:
    """Запускает сохранение состояния в отдельном потоке (не блокирует)."""
    # собираем словарь (копируем)
    with STATE_LOCK:
        data = {
            "user_sessions": user_sessions,
            "user_models": user_models,
            "user_styles": user_styles,
            "user_stickers": user_stickers,
            "meta": meta
        }

    def _worker(d):
        _write_state_file(d)
        log.debug("State saved to disk.")

    t = threading.Thread(target=_worker, args=(data,), daemon=True)
    t.start()

def save_state_blocking() -> None:
    """Иногда нужно явно сохранить синхронно (например перед shutdown)."""
    with STATE_LOCK:
        data = {
            "user_sessions": user_sessions,
            "user_models": user_models,
            "user_styles": user_styles,
            "user_stickers": user_stickers,
            "meta": meta
        }
    _write_state_file(data)
    log.info("State saved (blocking).")

# Загружаем состояние при старте
load_state()

# === ФОРМАТИРОВАНИЕ ===

def extract_code_blocks(text: str) -> List[Tuple[str, str]]:
    blocks = []
    i = 0
    while True:
        start = text.find("```", i)
        if start == -1:
            break
        lang_end = text.find("\n", start + 3)
        if lang_end == -1:
            break
        lang = text[start + 3:lang_end].strip()
        end = text.find("```", lang_end + 1)
        if end == -1:
            break
        code = text[lang_end + 1:end]
        blocks.append((lang, code))
        i = end + 3
    return blocks

def split_telegram_html(html_text: str, limit: int = 3500) -> List[str]:
    parts = []
    text = html_text
    while len(text) > limit:
        cut = text.rfind("\n\n", 0, limit)
        if cut == -1:
            cut = text.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit
        parts.append(text[:cut])
        text = text[cut:]
    if text:
        parts.append(text)
    return parts

def to_html_message(text: str, style: str = "auto") -> List[str]:
    blocks = extract_code_blocks(text)
    html_parts = []

    if style == "auto" and blocks:
        result_html = []
        i = 0
        while True:
            start = text.find("```", i)
            if start == -1:
                tail = html.escape(text[i:]).strip()
                if tail:
                    result_html.append(tail)
                break

            normal = html.escape(text[i:start]).strip()
            if normal:
                result_html.append(normal)

            lang_end = text.find("\n", start+3)
            end = text.find("```", lang_end+1)
            if end == -1:
                leftover = html.escape(text[start:]).strip()
                if leftover:
                    result_html.append(leftover)
                break

            lang = text[start+3:lang_end].strip()
            code = text[lang_end+1:end]
            code_html = html.escape(code)

            if lang:
                result_html.append(f"<b>Код ({html.escape(lang)}):</b>")
            result_html.append(f"<pre><code>{code_html}</code></pre>")

            i = end + 3

        final_html = "\n\n".join(result_html)
        html_parts = split_telegram_html(final_html)

    else:
        html_text = html.escape(text)
        html_parts = split_telegram_html(html_text)

    return html_parts

# === Индикатор печати ===
async def typing_indicator_loop(chat_id: int, context: ContextTypes.DEFAULT_TYPE, stop_event: asyncio.Event):
    try:
        while not stop_event.is_set():
            await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
            await asyncio.sleep(TYPING_PULSE_SEC)
    except Exception as e:
        log.warning(f"Typing indicator error: {e}")

# === Стикеры и утилиты ===
def get_user_key(user_id: int) -> str:
    return str(user_id)

def get_sticker_for(user_id: int, kind: str) -> str:
    """Возвращает sticker_id; сначала проверяет пользовательский, иначе дефолт."""
    key = get_user_key(user_id)
    user_sticker = user_stickers.get(key)
    if user_sticker:
        return user_sticker
    return DEFAULT_STICKERS.get(kind)

async def try_send_sticker(context: ContextTypes.DEFAULT_TYPE, chat_id: int, sticker_id: str):
    if not sticker_id:
        return
    try:
        await context.bot.send_sticker(chat_id=chat_id, sticker=sticker_id)
    except Exception as e:
        log.debug(f"Не удалось отправить стикер {sticker_id}: {e}")

# === OpenRouter ===
def chat_with_openrouter(user_id: int, message: str) -> str:

    key = get_user_key(user_id)
    # ensure structures exist
    user_sessions.setdefault(key, [])
    user_models.setdefault(key, DEFAULT_MODEL)
    user_styles.setdefault(key, DEFAULT_CODE_STYLE)

    user_sessions[key].append({"role": "user", "content": message})
    # persist quick
    save_state_async()

    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://example.com",
                "X-Title": BOT_NAME
            },
            json={
                "model": user_models.get(key, DEFAULT_MODEL),
                "messages": user_sessions[key]
            },
            timeout=45
        )

        r.raise_for_status()
        data = r.json()
        # обработка возможных форматов ответа (защита от неожиданных структур)
        reply = ""
        try:
            reply = data["choices"][0]["message"]["content"]
        except Exception:
            # попытка достать альтернативно
            reply = data.get("choices", [{}])[0].get("text", "")
            if not reply:
                # полная сериализация для диагностики
                reply = json.dumps(data, ensure_ascii=False)[:4000]

        user_sessions[key].append({"role": "assistant", "content": reply})
        save_state_async()
        return reply

    except Exception as e:
        log.exception("Error contacting OpenRouter")
        return f"⚠ Ошибка: {e}"

# === Команды ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Новый чат", callback_data="newchat_cb"),
         InlineKeyboardButton("Статус", callback_data="status_cb")],
        [InlineKeyboardButton("Настройка модели", callback_data="config_hint_cb"),
         InlineKeyboardButton("Стиль кода", callback_data="style_hint_cb")]
    ])
    # отправляем стикер (пользовательский или дефолтный)
    sticker = get_sticker_for(user_id, "greeting")
    if sticker:
        await try_send_sticker(context, update.effective_chat.id, sticker)

    await update.message.reply_text(
        f"Привет! Я {BOT_NAME}. Пиши.\n\n"
        "Команды: /help, /newchat, /clear, /config <model>, /style <auto|plain>, /setsticker <sticker_id>, /mysticker, /resetsticker, /export, /import",
        reply_markup=kb
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Проверить статус", callback_data="status_cb"),
         InlineKeyboardButton("Новый чат", callback_data="newchat_cb")],
        [InlineKeyboardButton("Сменить модель", callback_data="config_hint_cb"),
         InlineKeyboardButton("Стиль кода", callback_data="style_hint_cb")]
    ])

    await update.message.reply_text(
        "/start — привет\n"
        "/help — команды\n"
        "/newchat — сброс контекста\n"
        "/clear — очистить историю\n"
        "/config <model>\n"
        "/style <auto|plain>\n"
        "/status — статус API\n"
        "/setsticker <sticker_id> — установить ваш sticker_id для ответов\n"
        "/mysticker — показать ваш sticker_id\n"
        "/resetsticker — убрать ваш sticker_id\n"
        "/export — получить JSON с памятью (файл)\n"
        "/import — отправьте JSON-файл для импорта",
        reply_markup=kb
    )

async def newchat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    key = get_user_key(user_id)
    user_sessions[key] = []
    save_state_async()
    sticker = get_sticker_for(user_id, "newchat")
    if sticker:
        await try_send_sticker(context, update.effective_chat.id, sticker)
    await update.message.reply_text("Контекст очищен.")

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    key = get_user_key(user_id)
    user_sessions[key] = []
    save_state_async()
    sticker = get_sticker_for(user_id, "clear")
    if sticker:
        await try_send_sticker(context, update.effective_chat.id, sticker)
    await update.message.reply_text("История удалена.")

async def config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    key = get_user_key(user_id)
    if context.args:
        user_models[key] = context.args[0]
        save_state_async()
        await update.message.reply_text(f"Модель установлена: {context.args[0]}")
    else:
        await update.message.reply_text("Используй: /config openai/gpt-4o-mini")

async def style(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    key = get_user_key(user_id)
    if not context.args:
        return await update.message.reply_text("Используй: /style auto или /style plain")
    st = context.args[0].lower()
    if st not in ("auto", "plain"):
        return await update.message.reply_text("Только auto или plain!")
    user_styles[key] = st
    save_state_async()
    await update.message.reply_text(f"Стиль: {st}")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    key = get_user_key(user_id)
    model = user_models.get(key, DEFAULT_MODEL)
    style_value = user_styles.get(key, DEFAULT_CODE_STYLE)

    try:
        r = requests.get(
            "https://openrouter.ai/api/v1/models",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
            timeout=10
        )

        if r.status_code == 200:
            await update.message.reply_text(f"✅ API OK\nМодель: {model}\nСтиль: {style_value}")
        else:
            await update.message.reply_text(f"⚠ API код: {r.status_code}")
    except Exception as e:
        await update.message.reply_text(f"⚠ Ошибка: {e}")

# === Callback кнопок ===

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data
    user_id = q.from_user.id

    if data == "status_cb":
        await q.answer("Проверяю…")
        await status(update, context)
        return

    if data == "newchat_cb":
        key = get_user_key(user_id)
        user_sessions[key] = []
        save_state_async()
        await q.answer("Очищено")
        await q.edit_message_text("Контекст сброшен.")
        return

    if data == "config_hint_cb":
        await q.edit_message_text(
            "Примеры моделей:\n"
            "openai/gpt-4o-mini\n"
            "openai/gpt-4.1\n"
            "anthropic/claude-3.5\n\n"
            "Используй: /config <model>"
        )
        return

    if data == "style_hint_cb":
        await q.edit_message_text(
            "Стиль форматирования:\n"
            "auto — красиво\n"
            "plain — простой текст\n\n"
            "Используй: /style auto"
        )
        return

    await q.answer("Неизвестно.")

# === Управление стикерами и экспорт/импорт памяти ===

async def setsticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    key = get_user_key(user_id)
    if not context.args:
        return await update.message.reply_text("Использование: /setsticker <sticker_file_id_or_emoji_pack_id>")
    sticker_id = context.args[0].strip()
    user_stickers[key] = sticker_id
    save_state_async()
    await update.message.reply_text(f"Стикер сохранён: {sticker_id}")

async def mysticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    key = get_user_key(user_id)
    s = user_stickers.get(key)
    if s:
        # попытаемся отправить как стикер
        await try_send_sticker(context, update.effective_chat.id, s)
        await update.message.reply_text(f"Ваш sticker_id: {s}")
    else:
        await update.message.reply_text("У вас не установлен пользовательский стикер.")

async def resetsticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    key = get_user_key(user_id)
    if key in user_stickers:
        del user_stickers[key]
        save_state_async()
    await update.message.reply_text("Ваш стикер сброшен на дефолт.")

async def export_state(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет файл с текущим состоянием (всему пользователю)."""
    # формируем временный файл
    with STATE_LOCK:
        data = {
            "user_sessions": user_sessions,
            "user_models": user_models,
            "user_styles": user_styles,
            "user_stickers": user_stickers,
            "meta": meta
        }
    tmp = STATE_FILE.with_suffix(".export.json")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        await update.message.reply_document(document=InputFile(tmp), filename="bot_state_export.json")
    except Exception as e:
        await update.message.reply_text(f"Ошибка при экспорте: {e}")
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass

async def import_state_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает загруженный файл: ожидается JSON с полями, аналогичными экспорту."""
    # пользователь должен прислать файл как документ
    if not update.message.document:
        return await update.message.reply_text("Пришлите JSON файл с экспортом состояния.")
    doc = update.message.document
    if not doc.file_name.lower().endswith(".json"):
        return await update.message.reply_text("Файл должен быть .json")
    try:
        f = await doc.get_file()
        content = await f.download_as_bytearray()
        data = json.loads(content.decode("utf-8"))
        # validate minimal shape
        with STATE_LOCK:
            user_sessions.clear()
            user_models.clear()
            user_styles.clear()
            user_stickers.clear()
            user_sessions.update(data.get("user_sessions", {}))
            user_models.update(data.get("user_models", {}))
            user_styles.update(data.get("user_styles", {}))
            user_stickers.update(data.get("user_stickers", {}))
            meta.update(data.get("meta", {}))
        save_state_async()
        await update.message.reply_text("Импорт выполнен успешно.")
    except Exception as e:
        log.exception("Ошибка импорта")
        await update.message.reply_text(f"Ошибка импорта: {e}")

# === Сообщения ===

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.message.from_user.id
    text = (update.message.text or "").strip()
    if not text:
        return  # ничего не делаем с пустыми сообщениями

    stop_event = asyncio.Event()
    typing_task = asyncio.create_task(typing_indicator_loop(update.effective_chat.id, context, stop_event))

    try:
        # делаем запрос в sync-функцию в отдельном потоке
        reply = await asyncio.to_thread(chat_with_openrouter, user_id, text)

        style_value = user_styles.get(get_user_key(user_id), DEFAULT_CODE_STYLE)
        html_parts = to_html_message(reply, style=style_value)

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Новый чат", callback_data="newchat_cb"),
             InlineKeyboardButton("Статус", callback_data="status_cb")]
        ])

        # перед ответом — короткий стикер (reply), если есть
        sticker = get_sticker_for(user_id, "reply")
        if sticker:
            # send sticker but do not fail whole flow if sticker sending fails
            try:
                await context.bot.send_sticker(chat_id=update.effective_chat.id, sticker=sticker)
            except Exception as e:
                log.debug(f"Не удалось отправить стикер перед ответом: {e}")

        for i, part in enumerate(html_parts):
            await update.message.reply_text(
                part,
                parse_mode=ParseMode.HTML,
                reply_markup=kb if i == len(html_parts)-1 else None,
                disable_web_page_preview=True
            )

    except Exception as e:
        log.exception("Ошибка в handle_message")
        # отправляем стикер ошибки и сообщение
        sticker = get_sticker_for(user_id, "error")
        if sticker:
            try:
                await context.bot.send_sticker(chat_id=update.effective_chat.id, sticker=sticker)
            except Exception:
                pass
        await update.message.reply_text(f"Произошла ошибка: {e}")
    finally:
        stop_event.set()
        typing_task.cancel()

# === MAIN ===
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("newchat", newchat))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CommandHandler("config", config))
    app.add_handler(CommandHandler("style", style))
    app.add_handler(CommandHandler("status", status))

    # Стикеры и память
    app.add_handler(CommandHandler("setsticker", setsticker))
    app.add_handler(CommandHandler("mysticker", mysticker))
    app.add_handler(CommandHandler("resetsticker", resetsticker))
    app.add_handler(CommandHandler("export", export_state))
    app.add_handler(MessageHandler(filters.Document.FileExtension("json") & filters.ChatType.PRIVATE, import_state_file))
    app.add_handler(CommandHandler("import", lambda u, c: u.message.reply_text("Пришлите .json-файл как документ для импорта.")))

    app.add_handler(CallbackQueryHandler(on_callback))

    # Сообщения: текстовые сообщения направляем на основной обработчик
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.COMMAND, help_command))

    # лог запуск
    print(f"{BOT_NAME} запущен...")
    try:
        app.run_polling()
    finally:
        # при завершении сохраняем всё
        save_state_blocking()

if __name__ == "__main__":
    main()
