# bot.py
# Deluxe Metro Shop – Telegram bot for Metro Royal (PUBG Mobile)
# Requirements:
#   pip install pyTelegramBotAPI groq
#
# ENV:
#   set BOT_TOKEN=...
#   set GROQ_API_KEY=...
#
# Vision model:
#   meta-llama/llama-4-scout-17b-16e-instruct

import telebot
from telebot import types
import json
import os
import sys
import time
import hashlib
import traceback
import re
from typing import Any, Dict, List, Optional, Tuple
from groq import Groq

from flask import Flask
import threading

# ================== НАСТРОЙКИ ==================

TOKEN = os.environ.get("BOT_TOKEN", "").strip()
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()

if not TOKEN:
    raise RuntimeError("BOT_TOKEN is not set. Please export BOT_TOKEN env var.")
if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY is not set. Please export GROQ_API_KEY env var.")

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")
groq_client = Groq(api_key=GROQ_API_KEY)

DATA_DIR = "."
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
PRODUCTS_FILE = os.path.join(DATA_DIR, "products.json")
USERS_FILE = os.path.join(DATA_DIR, "users.json")
ORDERS_FILE = os.path.join(DATA_DIR, "orders.json")
LOGS_FILE = os.path.join(DATA_DIR, "logs.json")
AI_MEMORY_FILE = os.path.join(DATA_DIR, "ai_memory.json")

COOLDOWN_SECONDS = 3
PAGINATION_PAGE_SIZE = 6
DESCRIPTION_PREVIEW_LEN = 160

last_clean_message: Dict[int, int] = {}
last_activity: Dict[int, float] = {}
states: Dict[int, Dict[str, Any]] = {}  # unified state machine

pending_ai_actions: Dict[str, Dict[str, Any]] = {}

# ================== JSON HELPERS ==================

def ensure_files():
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "admin_password": "1234",
                "payment_phone": "TMT_PHONE",
                "order_manager_username": "order_manager_username",  # support username
                "super_admin_ids": [],
                "restart_script_path": "c:/Users/Admin/Desktop/magazin/bot.py"
            }, f, ensure_ascii=False, indent=2)

    for path, default in [
        (PRODUCTS_FILE, []),
        (USERS_FILE, {}),
        (ORDERS_FILE, []),
        (LOGS_FILE, []),
        (AI_MEMORY_FILE, {})
    ]:
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                json.dump(default, f, ensure_ascii=False, indent=2)

def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_config():
    return load_json(CONFIG_FILE, {})

def save_config(cfg):
    save_json(CONFIG_FILE, cfg)

def get_products():
    return load_json(PRODUCTS_FILE, [])

def save_products(products):
    save_json(PRODUCTS_FILE, products)

def get_users():
    return load_json(USERS_FILE, {})

def save_users(users):
    save_json(USERS_FILE, users)

def get_orders():
    return load_json(ORDERS_FILE, [])

def save_orders(orders):
    save_json(ORDERS_FILE, orders)

def get_logs():
    return load_json(LOGS_FILE, [])

def save_logs(logs):
    save_json(LOGS_FILE, logs)

# ================== LOGS ==================

def now_ts() -> int:
    return int(time.time())

def log_event(event_type, user_id=None, extra=None):
    logs = get_logs()
    logs.append({
        "timestamp": now_ts(),
        "type": event_type,
        "user_id": user_id,
        "extra": extra or {}
    })
    if len(logs) > 5000:
        logs = logs[-5000:]
    save_logs(logs)

def log_error(where: str, err: Exception, user_id: Optional[int] = None, extra: Optional[dict] = None):
    log_event("error", user_id=user_id, extra={
        "where": where,
        "error": repr(err),
        "traceback": traceback.format_exc()[-4000:],
        **(extra or {})
    })

# ================== SAFE EXECUTION ==================

GENERIC_ERROR_TEXT = "Ой, что-то пошло не так. Попробуйте ещё раз."

def safe_execute(where: str, user_id: Optional[int], chat_id: Optional[int], fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        log_error(where, e, user_id=user_id)
        if chat_id is not None:
            try:
                bot.send_message(chat_id, GENERIC_ERROR_TEXT)
            except Exception:
                pass
        return None

# ================== UTILS ==================

def safe_html(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def short_hash(obj: Any) -> str:
    s = json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha1(s).hexdigest()[:10]

def extract_first_fenced_block(text: str, fence: str = "bash") -> str:
    if not text:
        return text
    marker = f"```{fence}"
    i = text.find(marker)
    if i == -1:
        return text.strip()
    end = text.find("```", i + len(marker))
    if end == -1:
        return text[i:].strip()
    end2 = text.find("```", end)
    if end2 == -1:
        return text[i:].strip()
    return text[i:end2 + 3].strip()

def normalize_description(desc: str) -> str:
    """
    Remove AI mentions and clean whitespace. Keep it 'human store style'.
    """
    if not desc:
        return ""
    d = desc.strip()

    patterns = [
        r"\bкак\s+ии\b",
        r"\bя\s+как\s+ии\b",
        r"\bя\s+—\s+ии\b",
        r"\bсгенерировано\s+ии\b",
        r"\bискусственн(ый|ая)\s+интеллект\b",
        r"\bchatgpt\b",
        r"\bllama\b"
    ]
    for p in patterns:
        d = re.sub(p, "", d, flags=re.IGNORECASE)

    d = re.sub(r"[ \t]+", " ", d)
    d = re.sub(r"\n{3,}", "\n\n", d)
    return d.strip()

def preview_description(desc: str, limit: int = DESCRIPTION_PREVIEW_LEN) -> str:
    d = (desc or "").strip()
    if not d:
        return ""
    d = d.replace("\r", "")
    if len(d) <= limit:
        return d
    return d[:limit].rstrip() + "…"

def restart_self():
    cfg = get_config()
    script = (cfg.get("restart_script_path") or "").strip()
    if not script:
        script = os.path.abspath(__file__)
    script = os.path.abspath(script)
    argv = [sys.executable, script]
    log_event("bot_restart", extra={"argv": argv})
    os.execv(sys.executable, argv)

# ================== STATES ==================

def set_state(user_id: int, action: str, step: int = 0, data: Optional[dict] = None):
    states[user_id] = {"action": action, "step": step, "data": data or {}}

def get_state(user_id: int) -> Optional[dict]:
    return states.get(user_id)

def clear_state(user_id: int):
    states.pop(user_id, None)

# ================== ANTISPAM ==================

def check_cooldown(user_id):
    t = time.time()
    last = last_activity.get(user_id, 0)
    if t - last < COOLDOWN_SECONDS:
        return False
    last_activity[user_id] = t
    return True

def cooldown_guard(func):
    def wrapper(message_or_call):
        uid = message_or_call.from_user.id
        if not check_cooldown(uid):
            try:
                if isinstance(message_or_call, telebot.types.Message):
                    bot.reply_to(message_or_call, "⏳ Подождите 3 секунды.")
                else:
                    bot.answer_callback_query(message_or_call.id, "⏳ Подождите 3 секунды.", show_alert=True)
            except Exception:
                pass
            return

        chat_id = None
        try:
            if isinstance(message_or_call, telebot.types.CallbackQuery):
                chat_id = message_or_call.message.chat.id
            else:
                chat_id = message_or_call.chat.id
        except Exception:
            chat_id = None

        return safe_execute(func.__name__, uid, chat_id, func, message_or_call)
    return wrapper

# ================== SEND CLEAN ==================

def send_clean(chat_id, text, reply_markup=None, disable_web_page_preview=True):
    global last_clean_message

    if isinstance(reply_markup, types.ReplyKeyboardMarkup):
        return bot.send_message(chat_id, text, reply_markup=reply_markup, disable_web_page_preview=disable_web_page_preview)

    old = last_clean_message.get(chat_id)
    if old:
        try:
            bot.delete_message(chat_id, old)
        except Exception:
            pass

    msg = bot.send_message(chat_id, text, reply_markup=reply_markup, disable_web_page_preview=disable_web_page_preview)
    last_clean_message[chat_id] = msg.message_id
    return msg

# ================== DATA MODEL HELPERS ==================

def get_or_create_user(user_id, username):
    users = get_users()
    uid = str(user_id)
    if uid not in users:
        users[uid] = {
            "username": username or "",
            "cart": [],
            "is_admin": False,
            "awaiting_payment_order_id": None
        }
        save_users(users)
        log_event("new_user", user_id=user_id, extra={"username": username})
    else:
        if username and users[uid].get("username") != username:
            users[uid]["username"] = username
            save_users(users)
    return users[uid]

def update_user(user_id, data):
    users = get_users()
    uid = str(user_id)
    if uid not in users:
        users[uid] = {"username": "", "cart": [], "is_admin": False, "awaiting_payment_order_id": None}
    users[uid].update(data)
    save_users(users)

def add_to_cart(user_id, product_id):
    users = get_users()
    uid = str(user_id)
    if uid not in users:
        return
    users[uid].setdefault("cart", [])
    users[uid]["cart"].append(product_id)
    save_users(users)
    log_event("add_to_cart", user_id=user_id, extra={"product_id": product_id})

def clear_cart(user_id):
    users = get_users()
    uid = str(user_id)
    if uid not in users:
        return
    users[uid]["cart"] = []
    save_users(users)
    log_event("clear_cart", user_id=user_id)

def get_cart_items(user_id):
    users = get_users()
    uid = str(user_id)
    if uid not in users:
        return []
    cart_ids = users[uid].get("cart", [])
    products = get_products()
    by_id = {p.get("id"): p for p in products if isinstance(p, dict)}
    return [by_id[pid] for pid in cart_ids if pid in by_id]

def generate_product_id(products):
    ids = [p.get("id") for p in products if isinstance(p, dict)]
    new_id = 1
    while new_id in ids:
        new_id += 1
    return new_id

def generate_order_id(orders):
    ids = [o.get("id") for o in orders if isinstance(o, dict)]
    new_id = 1
    while new_id in ids:
        new_id += 1
    return new_id

def find_product_by_id(pid: int) -> Optional[Dict[str, Any]]:
    for p in get_products():
        if p.get("id") == pid:
            return p
    return None

# ================== ADMINS ==================

def is_admin(user_id):
    users = get_users()
    return str(user_id) in users and users[str(user_id)].get("is_admin", False)

def get_admin_ids():
    return [int(uid) for uid, u in get_users().items() if u.get("is_admin")]

def user_chat_url(username: str) -> Optional[str]:
    if not username:
        return None
    return f"https://t.me/{username}"

def support_url() -> Optional[str]:
    cfg = get_config()
    u = (cfg.get("order_manager_username") or "").strip().replace("@", "")
    if not u:
        return None
    return f"https://t.me/{u}"

def payment_manager_button():
    url = support_url()
    kb = types.InlineKeyboardMarkup()
    if url:
        kb.add(types.InlineKeyboardButton("🆘 Поддержка", url=url))
    return kb

# ================== UI: MENUS ==================

def main_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("🛒 Магазин", "🧭 Сопровождения")
    kb.row("🧺 Корзина", "📞 Оплата")
    kb.row("ℹ️ О магазине", "❓ Помощь")
    return kb

def shop_menu():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🔫 Оружие", callback_data="shop_weapon_list_0"))
    kb.add(types.InlineKeyboardButton("🛡 Броня", callback_data="shop_armor_list_0"))
    kb.add(types.InlineKeyboardButton("🏠 Главное меню", callback_data="back_main_menu"))
    return kb

def escort_menu_inline():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🧭 Сопровождения", callback_data="shop_escort_list_0"))
    kb.add(types.InlineKeyboardButton("🏠 Главное меню", callback_data="back_main_menu"))
    return kb

def admin_main_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("📦 Управление товарами", "🧭 Управление сопровождениями")
    kb.row("⚙️ Настройки", "📢 Рассылка")
    kb.row("📊 Статистика", "📜 Логи бота")
    kb.row("🤖 ИИ‑панель", "👥 Админы")
    kb.row("🔄 Перезапустить бота", "⬅️ В главное меню")
    return kb

def admin_products_menu():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("➕ Добавить товар", callback_data="admin_add_product"))
    kb.add(types.InlineKeyboardButton("❌ Удалить товар", callback_data="admin_delete_product"))
    kb.add(types.InlineKeyboardButton("✏️ Изменить цену", callback_data="admin_change_price"))
    kb.add(types.InlineKeyboardButton("✏️ Изменить описание", callback_data="admin_change_desc"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="admin_back_main"))
    return kb

def admin_escort_menu():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("➕ Добавить сопровождение", callback_data="admin_add_escort"))
    kb.add(types.InlineKeyboardButton("❌ Удалить сопровождение", callback_data="admin_delete_escort"))
    kb.add(types.InlineKeyboardButton("✏️ Изменить цену", callback_data="admin_change_escort_price"))
    kb.add(types.InlineKeyboardButton("✏️ Изменить описание", callback_data="admin_change_desc"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="admin_back_main"))
    return kb

def admin_settings_menu():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🔐 Изменить пароль", callback_data="admin_change_password"))
    kb.add(types.InlineKeyboardButton("📞 Изменить номер оплаты", callback_data="admin_change_payment_phone"))
    kb.add(types.InlineKeyboardButton("👤 Изменить username поддержки", callback_data="admin_change_manager_username"))
    kb.add(types.InlineKeyboardButton("🗑 Очистить заказы", callback_data="admin_clear_orders"))
    kb.add(types.InlineKeyboardButton("🔄 Перезапустить бота", callback_data="admin_restart_bot"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="admin_back_main"))
    return kb

def admin_logs_menu():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🕐 1 час", callback_data="admin_logs_1h"))
    kb.add(types.InlineKeyboardButton("📅 24 часа", callback_data="admin_logs_24h"))
    kb.add(types.InlineKeyboardButton("📆 7 дней", callback_data="admin_logs_7d"))
    kb.add(types.InlineKeyboardButton("🌐 Все (огр.)", callback_data="admin_logs_all"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="admin_back_main"))
    return kb

def admin_admins_menu():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("➕ Добавить админа по ID", callback_data="admin_add_admin"))
    kb.add(types.InlineKeyboardButton("➖ Удалить админа по ID", callback_data="admin_remove_admin"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="admin_back_main"))
    return kb

def admin_ai_menu():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🧾 Инфо Vision", callback_data="ai_info_scout"))
    kb.add(types.InlineKeyboardButton("🧰 ИИ‑оператор (план+подтверждение)", callback_data="ai_operator_full"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="admin_back_main"))
    return kb

# ================== SHOP LIST + PRODUCT PAGES (NEW) ==================

def paginate_products(ptype: str) -> List[Dict[str, Any]]:
    return [p for p in get_products() if p.get("type") == ptype]

def render_products_list_text(ptype: str, page: int) -> Tuple[str, int]:
    items = paginate_products(ptype)
    total = len(items)
    pages = max(1, (total + PAGINATION_PAGE_SIZE - 1) // PAGINATION_PAGE_SIZE)
    page = max(0, min(page, pages - 1))

    start = page * PAGINATION_PAGE_SIZE
    end = start + PAGINATION_PAGE_SIZE
    part = items[start:end]

    title_map = {"weapon": "🔫 <b>Оружие</b>", "armor": "🛡 <b>Броня</b>", "escort": "🧭 <b>Сопровождения</b>"}
    header = title_map.get(ptype, "<b>Список</b>")
    lines = [header, f"Страница: <b>{page+1}/{pages}</b>\n"]
    lines.append("Нажмите на товар, чтобы открыть его страницу.")

    if not part:
        lines.append("\nПока пусто.")
    else:
        for p in part:
            pid = p.get("id")
            desc = preview_description(p.get("description", ""))
            lines.append(
                f"\n• <b>{safe_html(p.get('title',''))}</b> — <b>{p.get('price','?')} TMT</b>"
                + (f" | {safe_html(p.get('category',''))}" if p.get("category") else "")
                + f"\n   ID: <code>{pid}</code>"
                + (f"\n   {safe_html(desc)}" if desc else "")
            )
    return "\n".join(lines), pages

def products_list_kb(ptype: str, page: int, pages: int) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    items = paginate_products(ptype)
    start = page * PAGINATION_PAGE_SIZE
    end = start + PAGINATION_PAGE_SIZE
    part = items[start:end]

    for p in part:
        pid = p.get("id")
        title = p.get("title", "")
        kb.add(types.InlineKeyboardButton(f"📄 {title}", callback_data=f"prod_open_{ptype}_{pid}"))

    nav = []
    if page > 0:
        nav.append(types.InlineKeyboardButton("⬅️ Назад", callback_data=f"shop_{ptype}_list_{page-1}"))
    if page < pages - 1:
        nav.append(types.InlineKeyboardButton("➡️ Вперёд", callback_data=f"shop_{ptype}_list_{page+1}"))
    if nav:
        kb.row(*nav)

    kb.add(types.InlineKeyboardButton("➕ Добавить по ID в корзину", callback_data=f"shop_addbyid_{ptype}"))
    kb.add(types.InlineKeyboardButton("🧺 Корзина", callback_data="open_cart"))
    kb.add(types.InlineKeyboardButton("🏠 Главное меню", callback_data="back_main_menu"))
    return kb

def render_product_page_text(p: Dict[str, Any], idx: int, total: int, ptype: str) -> str:
    title_map = {"weapon": "🔫", "armor": "🛡", "escort": "🧭"}
    icon = title_map.get(ptype, "📦")
    lines = [
        f"{icon} <b>{safe_html(p.get('title',''))}</b>",
        f"Категория: <b>{safe_html(p.get('category',''))}</b>" if p.get("category") else "Категория: <b>—</b>",
        f"Цена: <b>{p.get('price','?')} TMT</b>",
        f"ID: <code>{p.get('id')}</code>",
        f"\nСтраница товара: <b>{idx+1}/{total}</b>",
    ]
    desc = (p.get("description") or "").strip()
    if desc:
        lines.append("\n<b>Описание:</b>\n" + safe_html(desc))
    else:
        lines.append("\n<b>Описание:</b>\n—")
    return "\n".join(lines)

def product_page_kb(ptype: str, idx: int, total: int, pid: int) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    nav = []
    if idx > 0:
        nav.append(types.InlineKeyboardButton("⬅️ Назад", callback_data=f"prod_nav_{ptype}_{idx-1}"))
    if idx < total - 1:
        nav.append(types.InlineKeyboardButton("➡️ Вперёд", callback_data=f"prod_nav_{ptype}_{idx+1}"))
    if nav:
        kb.row(*nav)

    kb.add(types.InlineKeyboardButton("➕ Добавить в корзину", callback_data=f"prod_add_{pid}"))
    kb.add(types.InlineKeyboardButton("📋 К списку", callback_data=f"shop_{ptype}_list_0"))
    kb.add(types.InlineKeyboardButton("🧺 Корзина", callback_data="open_cart"))
    kb.add(types.InlineKeyboardButton("🏠 Главное меню", callback_data="back_main_menu"))
    return kb

def products_sorted_by_id(ptype: str) -> List[Dict[str, Any]]:
    items = paginate_products(ptype)
    try:
        return sorted(items, key=lambda x: int(x.get("id", 0)))
    except Exception:
        return items

def find_index_by_id(items: List[Dict[str, Any]], pid: int) -> int:
    for i, p in enumerate(items):
        if p.get("id") == pid:
            return i
    return 0

# ================== CART / ORDERS ==================

def show_cart(chat_id: int, user_id: int):
    items = get_cart_items(user_id)
    if not items:
        send_clean(chat_id, "🧺 Ваша корзина пуста.", reply_markup=main_menu())
        return
    total = sum(int(i.get("price", 0)) for i in items)
    lines = ["🧺 <b>Ваша корзина</b>:"]
    for i, it in enumerate(items, 1):
        lines.append(f"{i}. {safe_html(it.get('title',''))} — {it.get('price','?')} TMT")
    lines.append(f"\n💰 Итого: <b>{total} TMT</b>")
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✅ Оформить заказ", callback_data="cart_checkout"))
    kb.add(types.InlineKeyboardButton("🗑 Очистить", callback_data="cart_clear"))
    kb.add(types.InlineKeyboardButton("🏠 Главное меню", callback_data="back_main_menu"))
    send_clean(chat_id, "\n".join(lines), reply_markup=kb)

def create_order_for_user(user_id: int, username: Optional[str]):
    items = get_cart_items(user_id)
    if not items:
        return None
    orders = get_orders()
    order_id = generate_order_id(orders)
    total = sum(int(i.get("price", 0)) for i in items)
    order = {
        "id": order_id,
        "user_id": user_id,
        "username": username or "",
        "items": items,
        "total": total,
        "status": "pending_payment",
        "created_ts": now_ts(),
        "payment_photo_file_id": None,
        "ai_verdict_last": None
    }
    orders.append(order)
    save_orders(orders)
    log_event("order_created", user_id=user_id, extra={"order_id": order_id, "total": total})
    return order

def reject_order(admin_id: int, order_id: int, reason: str) -> bool:
    orders = get_orders()
    o = next((x for x in orders if x.get("id") == order_id), None)
    if not o:
        return False
    o["status"] = "rejected"
    o["reject_reason"] = reason
    o["rejected_ts"] = now_ts()
    save_orders(orders)
    log_event("order_rejected", user_id=admin_id, extra={"order_id": order_id, "reason": reason})

    try:
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("⚖️ Оспорить", callback_data=f"dispute_menu_{order_id}"))
        bot.send_message(
            o["user_id"],
            f"❌ Ваш заказ <b>#{order_id}</b> отклонён.\n"
            f"Причина: {safe_html(reason)}\n\n"
            "Если вы не согласны — нажмите «Оспорить».",
            reply_markup=kb
        )
    except Exception:
        pass
    return True

# ================== ADMIN: ORDER NOTIFY ==================

def send_order_log_to_admins(order):
    admins = get_admin_ids()
    if not admins:
        return

    msg_lines = [
        "📦 <b>Заказ на проверку оплаты</b>",
        f"🆔 ID заказа: <code>{order.get('id')}</code>",
        f"👤 User ID: <code>{order.get('user_id')}</code>",
        f"🔗 Username: @{order.get('username')}" if order.get("username") else "🔗 Username: (нет)",
        "🧾 Товары:"
    ]
    for item in order.get("items", []):
        msg_lines.append(
            f"• {safe_html(item.get('title','?'))} ({item.get('type','')}, {item.get('category','')}) — {item.get('price','?')} TMT"
        )
    msg_lines.append(f"\n💰 Сумма: <b>{order.get('total','?')} TMT</b>")
    msg_lines.append(f"📌 Статус: <b>{order.get('status','unknown')}</b>")
    msg = "\n".join(msg_lines)

    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🔍 Проверить (ИИ)", callback_data=f"check_payment_{order.get('id')}"))
    kb.add(types.InlineKeyboardButton("🧠 Подробно (ИИ)", callback_data=f"check_payment_deep_{order.get('id')}"))
    kb.add(types.InlineKeyboardButton("✉️ Сообщение клиенту", callback_data=f"order_msg_{order.get('id')}"))
    kb.add(types.InlineKeyboardButton("❌ Заказ отклонён", callback_data=f"order_reject_{order.get('id')}"))
    if order.get("username"):
        kb.add(types.InlineKeyboardButton("💬 Перейти в чат", url=user_chat_url(order["username"])))

    for admin_id in admins:
        try:
            bot.send_photo(admin_id, order["payment_photo_file_id"], caption=msg, reply_markup=kb)
        except Exception:
            try:
                bot.send_message(admin_id, msg, reply_markup=kb)
            except Exception:
                pass

# ================== ADMIN: DISPUTE REPLY (NEW) ==================

def dispute_admin_reply_kb(user_id: int, order_id: int) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("↩️ Ответить пользователю", callback_data=f"dispute_reply_{order_id}_{user_id}"))
    kb.add(types.InlineKeyboardButton("✉️ Сообщение клиенту (заказ)", callback_data=f"order_msg_{order_id}"))
    return kb

# ================== VISION ==================

def ai_check_payment_image(order: Dict[str, Any], file_url: str, deep: bool = False) -> str:
    mode = "DEEP" if deep else "STANDARD"
    instructions = (
        "Ты — эксперт по анализу фото/скриншотов подтверждения оплаты.\n"
        "Определи: настоящее ли изображение или подделка/монтаж/сгенерировано ИИ.\n"
        "Не выдумывай факты: если не видно — так и пиши.\n\n"
        f"Режим: {mode}\n"
        f"ID заказа: {order.get('id')}\n"
        f"Ожидаемая сумма: {order.get('total')} TMT\n\n"
        "Ответ строго ТОЛЬКО в формате:\n"
        "```bash\n"
        "Статус: [реальное / подозрительное / похоже на ИИ / недостаточно данных]\n"
        "Уверенность: [низкая/средняя/высокая]\n"
        "Совпадение суммы: [да/нет/не видно]\n"
        "Найденная сумма: [значение/не видно]\n"
        "Валюта: [TMT/другая/не видно]\n"
        "Получатель/номер: [значение/не видно]\n"
        "Статус операции: [успешно/ошибка/в обработке/не видно]\n"
        "Дата/время: [значение/не видно]\n"
        "Разбор:\n"
        "- ...\n"
        "Итог:\n"
        "- ...\n"
        "Рекомендация:\n"
        "- [принять/запросить доп.скрин/проверить вручную]\n"
        "```"
    )
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": instructions},
            {"type": "image_url", "image_url": {"url": file_url}}
        ]
    }]
    try:
        completion = groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=messages,
            temperature=0.2 if deep else 0.3,
            max_completion_tokens=1400 if deep else 700,
            top_p=1,
            stream=False,
            stop=None
        )
        raw = completion.choices[0].message.content.strip()
        return extract_first_fenced_block(raw, "bash")
    except Exception as e:
        log_error("ai_check_payment_image", e)
        return "```bash\nСтатус: недостаточно данных\nУверенность: низкая\nКомментарий: ошибка анализа\n```"

# ================== AI OPERATOR (IMPROVED PROMPT) ==================

AI_OPERATOR_MODEL = "llama-3.3-70b-versatile"

def build_full_context_for_ai() -> Dict[str, Any]:
    return {
        "config": get_config(),
        "products": get_products(),
        "users_count": len(get_users()),
        "orders": get_orders()[-100:],
        "logs_tail": get_logs()[-200:],
        "server_time": now_ts(),
    }

def ai_operator_system_prompt() -> str:
    return (
        "Ты — оператор админ-панели магазина Deluxe Metro Shop (Telegram).\n"
        "Твоя задача: составить ПЛАН действий в JSON для управления товарами/заказами/настройками.\n"
        "Никакого markdown и лишнего текста — только валидный JSON.\n\n"
        "КРИТИЧЕСКИ ВАЖНО:\n"
        "- Если добавляешь/обновляешь description: сделай красивое, продающее описание на русском, 2–6 коротких строк.\n"
        "- Структура описания:\n"
        "  1) 1 строка — что это за товар/услуга.\n"
        "  2) 2–4 буллета через дефис (преимущества/условия/что входит).\n"
        "  3) 1 строка — примечание/условие (если уместно).\n"
        "- Не используй упоминаний ИИ/моделей/ChatGPT/Llama.\n"
        "- Не выдумывай несуществующие поля. Используй только перечисленные Actions.\n\n"
        "Формат:\n"
        "{\n"
        '  "summary": "кратко что будет сделано",\n'
        '  "risk": "low|medium|high",\n'
        '  "actions": [ {"type":"...", "params":{...}} ]\n'
        "}\n\n"
        "Actions:\n"
        "- add_product {title, type: weapon|armor, category, price, description}\n"
        "- add_escort {title, category, price, description}\n"
        "- set_description {id, description}\n"
        "- clear_description {id}\n"
        "- delete_product {id}\n"
        "- delete_escort {id}\n"
        "- change_price {id, price}\n"
        "- change_escort_price {id, price}\n"
        "- set_payment_phone {phone}\n"
        "- set_manager_username {username}\n"
        "- add_admin {user_id}\n"
        "- remove_admin {user_id}\n"
        "- order_reject {order_id, reason}\n"
        "- send_message_to_user {user_id, text}\n"
        "- broadcast {text}\n"
        "- restart_bot {}\n"
        "- get_stats {}\n"
    )

def ai_parse_json_strict(text: str) -> Optional[Dict[str, Any]]:
    try:
        t = (text or "").strip()
        if t.startswith("```"):
            t = t.strip("`").replace("json\n", "", 1).strip()
        obj = json.loads(t)
        if not isinstance(obj, dict):
            return None
        if "actions" not in obj or not isinstance(obj["actions"], list):
            return None
        return obj
    except Exception:
        return None

def ai_operator_plan(admin_id: int, user_text: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    ctx = build_full_context_for_ai()
    messages = [
        {"role": "system", "content": ai_operator_system_prompt()},
        {"role": "system", "content": "КОНТЕКСТ:\n" + json.dumps(ctx, ensure_ascii=False)[:35000]},
        {"role": "user", "content": user_text}
    ]
    try:
        completion = groq_client.chat.completions.create(
            model=AI_OPERATOR_MODEL,
            messages=messages,
            temperature=0.12,
            max_completion_tokens=1800,
            top_p=1,
            stream=False,
            stop=None
        )
        raw = completion.choices[0].message.content.strip()
    except Exception as e:
        log_error("ai_operator_plan", e, user_id=admin_id)
        raw = ""
    obj = ai_parse_json_strict(raw)
    return raw, obj

def execute_operator_action(admin_id: int, action: Dict[str, Any]) -> Tuple[bool, str, bool]:
    a_type = action.get("type")
    params = action.get("params") or {}

    if a_type == "get_stats":
        users = get_users()
        orders = get_orders()
        products = get_products()
        stats = {
            "users": len(users),
            "orders": len(orders),
            "products": len(products),
            "weapons": len([p for p in products if p.get("type") == "weapon"]),
            "armors": len([p for p in products if p.get("type") == "armor"]),
            "escorts": len([p for p in products if p.get("type") == "escort"]),
        }
        return True, json.dumps(stats, ensure_ascii=False), False

    if a_type == "restart_bot":
        log_event("ai_restart_requested", user_id=admin_id)
        return True, "restart scheduled", True

    if a_type == "broadcast":
        txt = str(params.get("text", "")).strip()
        if not txt:
            return False, "broadcast text empty", False
        users = get_users()
        cnt = 0
        for uid in list(users.keys()):
            try:
                bot.send_message(int(uid), txt)
                cnt += 1
            except Exception:
                pass
        log_event("ai_broadcast", user_id=admin_id, extra={"sent": cnt})
        return True, f"broadcast sent to {cnt}", False

    if a_type == "set_payment_phone":
        phone = str(params.get("phone", "")).strip()
        if not phone:
            return False, "phone empty", False
        cfg = get_config()
        cfg["payment_phone"] = phone
        save_config(cfg)
        return True, "payment_phone updated", False

    if a_type == "set_manager_username":
        uname = str(params.get("username", "")).replace("@", "").strip()
        if not uname:
            return False, "username empty", False
        cfg = get_config()
        cfg["order_manager_username"] = uname
        save_config(cfg)
        return True, "support username updated", False

    if a_type == "add_admin":
        try:
            new_id = int(params.get("user_id"))
        except Exception:
            return False, "user_id must be int", False
        users = get_users()
        uid_str = str(new_id)
        users.setdefault(uid_str, {"username": "", "cart": [], "is_admin": False, "awaiting_payment_order_id": None})
        users[uid_str]["is_admin"] = True
        save_users(users)
        return True, f"user {new_id} is admin", False

    if a_type == "remove_admin":
        try:
            rem_id = int(params.get("user_id"))
        except Exception:
            return False, "user_id must be int", False
        users = get_users()
        uid_str = str(rem_id)
        if uid_str in users and users[uid_str].get("is_admin"):
            users[uid_str]["is_admin"] = False
            save_users(users)
            return True, f"user {rem_id} removed from admins", False
        return False, "not an admin", False

    if a_type == "order_reject":
        try:
            oid = int(params.get("order_id"))
        except Exception:
            return False, "order_id must be int", False
        reason = str(params.get("reason", "Оплата отклонена")).strip()
        ok = reject_order(admin_id, oid, reason)
        return ok, f"order #{oid} rejected", False

    if a_type == "send_message_to_user":
        try:
            uid = int(params.get("user_id"))
        except Exception:
            return False, "user_id must be int", False
        txt = str(params.get("text", "")).strip()
        if not txt:
            return False, "text empty", False
        try:
            bot.send_message(uid, txt)
        except Exception as e:
            return False, f"send failed: {e}", False
        return True, f"sent to {uid}", False

    if a_type == "add_product":
        title = str(params.get("title", "")).strip()
        ptype = str(params.get("type", "")).strip().lower()
        category = str(params.get("category", "")).strip()
        description = normalize_description(str(params.get("description", "")).strip())
        price = params.get("price", None)
        if not title or ptype not in ("weapon", "armor") or price is None:
            return False, "invalid params for add_product", False
        try:
            price = int(price)
            if price < 0:
                return False, "price must be >=0", False
        except Exception:
            return False, "price must be int", False
        products = get_products()
        pid = generate_product_id(products)
        products.append({"id": pid, "title": title, "type": ptype, "category": category, "price": price, "description": description})
        save_products(products)
        return True, f"added product id={pid}", False

    if a_type == "add_escort":
        title = str(params.get("title", "")).strip()
        category = str(params.get("category", "")).strip()
        description = normalize_description(str(params.get("description", "")).strip())
        price = params.get("price", None)
        if not title or price is None:
            return False, "invalid params for add_escort", False
        try:
            price = int(price)
            if price < 0:
                return False, "price must be >=0", False
        except Exception:
            return False, "price must be int", False
        products = get_products()
        pid = generate_product_id(products)
        products.append({"id": pid, "title": title, "type": "escort", "category": category, "price": price, "description": description})
        save_products(products)
        return True, f"added escort id={pid}", False

    if a_type == "set_description":
        try:
            pid = int(params.get("id"))
        except Exception:
            return False, "id must be int", False
        desc = normalize_description(str(params.get("description", "")).strip())
        products = get_products()
        p = next((x for x in products if x.get("id") == pid), None)
        if not p:
            return False, "not found", False
        p["description"] = desc
        save_products(products)
        return True, f"description updated id={pid}", False

    if a_type == "clear_description":
        try:
            pid = int(params.get("id"))
        except Exception:
            return False, "id must be int", False
        products = get_products()
        p = next((x for x in products if x.get("id") == pid), None)
        if not p:
            return False, "not found", False
        p["description"] = ""
        save_products(products)
        return True, f"description cleared id={pid}", False

    if a_type in ("delete_product", "delete_escort"):
        try:
            pid = int(params.get("id"))
        except Exception:
            return False, "id must be int", False
        products = get_products()
        p = next((x for x in products if x.get("id") == pid), None)
        if not p:
            return False, "not found", False
        if a_type == "delete_product" and p.get("type") not in ("weapon", "armor"):
            return False, "id is not weapon/armor", False
        if a_type == "delete_escort" and p.get("type") != "escort":
            return False, "id is not escort", False
        products = [x for x in products if x.get("id") != pid]
        save_products(products)
        return True, f"deleted id={pid}", False

    if a_type in ("change_price", "change_escort_price"):
        try:
            pid = int(params.get("id"))
            price = int(params.get("price"))
            if price < 0:
                return False, "price must be >=0", False
        except Exception:
            return False, "id/price must be int", False
        products = get_products()
        p = next((x for x in products if x.get("id") == pid), None)
        if not p:
            return False, "not found", False
        if a_type == "change_price" and p.get("type") not in ("weapon", "armor"):
            return False, "id is not weapon/armor", False
        if a_type == "change_escort_price" and p.get("type") != "escort":
            return False, "id is not escort", False
        p["price"] = price
        save_products(products)
        return True, f"updated id={pid} price={price}", False

    return False, f"unknown action: {a_type}", False

# ================== COMMANDS ==================

@bot.message_handler(commands=["start"])
@cooldown_guard
def start_handler(message):
    u = message.from_user
    get_or_create_user(u.id, u.username)
    send_clean(message.chat.id, "💎 <b>Deluxe Metro Shop</b>\n\nВыберите раздел:", reply_markup=main_menu())

@bot.message_handler(commands=["menu"])
@cooldown_guard
def menu_handler(message):
    send_clean(message.chat.id, "Главное меню:", reply_markup=main_menu())

@bot.message_handler(commands=["add"])
@cooldown_guard
def add_admin_handler(message):
    uid = message.from_user.id
    if is_admin(uid):
        send_clean(message.chat.id, "✅ Вы уже админ.", reply_markup=admin_main_menu())
        return
    bot.send_message(message.chat.id, "🔐 Введите пароль админки (или 'Отмена'):")
    set_state(uid, "admin_login")

# ================== PHOTO PAYMENT ==================

@bot.message_handler(content_types=["photo"])
@cooldown_guard
def photo_handler(message):
    uid = message.from_user.id
    users = get_users()
    suid = str(uid)

    if suid not in users or not users[suid].get("awaiting_payment_order_id"):
        bot.reply_to(message, "Фото не привязано к заказу. Сначала оформите заказ через корзину.")
        return

    order_id = users[suid]["awaiting_payment_order_id"]
    orders = get_orders()
    order = next((o for o in orders if o.get("id") == order_id), None)
    if not order:
        bot.reply_to(message, "Заказ не найден.")
        users[suid]["awaiting_payment_order_id"] = None
        save_users(users)
        return

    file_id = message.photo[-1].file_id
    order["payment_photo_file_id"] = file_id
    order["status"] = "awaiting_check"
    order["paid_photo_received_ts"] = now_ts()
    save_orders(orders)

    users[suid]["awaiting_payment_order_id"] = None
    save_users(users)

    bot.reply_to(message, "✅ Фото оплаты получено. Ожидайте проверки администратором.")
    send_order_log_to_admins(order)

# ================== TEXT ==================

@bot.message_handler(content_types=["text"])
@cooldown_guard
def text_handler(message):
    uid = message.from_user.id
    username = message.from_user.username
    get_or_create_user(uid, username)

    text = message.text.strip()
    st = get_state(uid)

    if st and text.lower() in ("отмена", "cancel", "⬅️ в главное меню"):
        clear_state(uid)
        if is_admin(uid):
            send_clean(message.chat.id, "Админ-панель:", reply_markup=admin_main_menu())
        else:
            send_clean(message.chat.id, "Главное меню:", reply_markup=main_menu())
        return

    if st:
        handle_state_text(message, st)
        return

    if text == "🛒 Магазин":
        send_clean(message.chat.id, "Выберите категорию:", reply_markup=shop_menu())
        return
    if text == "🧭 Сопровождения":
        send_clean(message.chat.id, "🧭 Сопровождения:", reply_markup=escort_menu_inline())
        return
    if text == "🧺 Корзина":
        show_cart(message.chat.id, uid)
        return
    if text == "📞 Оплата":
        cfg = get_config()
        send_clean(
            message.chat.id,
            f"📞 Номер для оплаты: <b>{safe_html(cfg.get('payment_phone','не указан'))}</b>\n\n"
            "После оформления заказа отправьте фото оплаты сюда.",
            reply_markup=payment_manager_button()
        )
        return
    if text == "ℹ️ О магазине":
        send_clean(message.chat.id, "ℹ️ <b>О магазине</b>\n\nDeluxe Metro Shop.", reply_markup=main_menu())
        return
    if text == "❓ Помощь":
        send_clean(message.chat.id, "❓ <b>Помощь</b>\n\nОформите заказ через корзину и отправьте фото оплаты.", reply_markup=main_menu())
        return
    if text == "⬅️ В главное меню":
        send_clean(message.chat.id, "Главное меню:", reply_markup=main_menu())
        return

    if text == "📦 Управление товарами" and is_admin(uid):
        send_clean(message.chat.id, "📦 Управление товарами:", reply_markup=admin_products_menu())
        return
    if text == "🧭 Управление сопровождениями" and is_admin(uid):
        send_clean(message.chat.id, "🧭 Управление сопровождениями:", reply_markup=admin_escort_menu())
        return
    if text == "⚙️ Настройки" and is_admin(uid):
        send_clean(message.chat.id, "⚙️ Настройки:", reply_markup=admin_settings_menu())
        return
    if text == "📢 Рассылка" and is_admin(uid):
        bot.send_message(message.chat.id, "Введите текст рассылки (или 'Отмена'):")
        set_state(uid, "broadcast")
        return
    if text == "📊 Статистика" and is_admin(uid):
        users = get_users()
        orders = get_orders()
        products = get_products()
        send_clean(
            message.chat.id,
            f"📊 <b>Статистика</b>\n\n"
            f"👥 Пользователей: <b>{len(users)}</b>\n"
            f"📦 Заказов: <b>{len(orders)}</b>\n"
            f"🛒 Товаров: <b>{len(products)}</b>",
            reply_markup=admin_main_menu()
        )
        return
    if text == "📜 Логи бота" and is_admin(uid):
        send_clean(message.chat.id, "📜 Выберите период:", reply_markup=admin_logs_menu())
        return
    if text == "👥 Админы" and is_admin(uid):
        users = get_users()
        admins = [(k, v) for k, v in users.items() if v.get("is_admin")]
        if admins:
            lines = ["👥 <b>Админы</b>:"]
            for k, v in admins:
                uname = v.get("username", "")
                lines.append(f"• <code>{k}</code>" + (f" @{uname}" if uname else ""))
            bot.send_message(message.chat.id, "\n".join(lines))
        else:
            bot.send_message(message.chat.id, "Админов пока нет.")
        bot.send_message(message.chat.id, "Выберите действие:", reply_markup=admin_admins_menu())
        return
    if text == "🤖 ИИ‑панель" and is_admin(uid):
        send_clean(message.chat.id, "🤖 ИИ‑панель:", reply_markup=admin_ai_menu())
        return
    if text == "🔄 Перезапустить бота" and is_admin(uid):
        bot.send_message(message.chat.id, "🔄 Перезапуск...")
        time.sleep(0.4)
        restart_self()
        return

    send_clean(message.chat.id, "Не понял. Используйте меню. /menu", reply_markup=main_menu())

# ================== STATE TEXT LOGIC ==================

def handle_state_text(message, st):
    uid = message.from_user.id
    chat_id = message.chat.id
    text = message.text.strip()
    action = st.get("action")
    step = st.get("step", 0)
    data = st.get("data", {})

    if action == "admin_login":
        cfg = get_config()
        if text == cfg.get("admin_password", "1234"):
            update_user(uid, {"is_admin": True})
            clear_state(uid)
            send_clean(chat_id, "✅ Вы админ.", reply_markup=admin_main_menu())
        else:
            bot.send_message(chat_id, "❌ Неверный пароль. Попробуйте ещё раз или «Отмена».")
        return

    if action == "broadcast":
        users = get_users()
        cnt = 0
        for u in list(users.keys()):
            try:
                bot.send_message(int(u), text)
                cnt += 1
            except Exception:
                pass
        clear_state(uid)
        send_clean(chat_id, f"✅ Отправлено: {cnt}", reply_markup=admin_main_menu())
        return

    if action == "user_add_to_cart_by_id":
        desired = data.get("ptype")
        try:
            pid = int(text)
        except Exception:
            bot.send_message(chat_id, "❌ Введите ID числом.")
            return
        p = find_product_by_id(pid)
        if not p:
            bot.send_message(chat_id, "❌ Товар не найден.")
            return
        if desired and p.get("type") != desired:
            bot.send_message(chat_id, "❌ ID не из выбранной категории.")
            return
        add_to_cart(uid, pid)
        clear_state(uid)
        bot.send_message(chat_id, "✅ Добавлено в корзину.")
        return

    if action == "user_dispute_message":
        oid = int(data.get("order_id"))
        msg = text
        admins = get_admin_ids()
        if not admins:
            bot.send_message(chat_id, "Поддержка сейчас недоступна. Попробуйте позже.")
            clear_state(uid)
            return
        for a in admins:
            try:
                bot.send_message(
                    a,
                    "⚖️ <b>Оспаривание заказа</b>\n"
                    f"Заказ: <code>#{oid}</code>\n"
                    f"От пользователя: <code>{uid}</code> @{message.from_user.username or 'нет'}\n\n"
                    f"Сообщение:\n{safe_html(msg)}",
                    reply_markup=dispute_admin_reply_kb(uid, oid)
                )
            except Exception:
                pass
        bot.send_message(chat_id, "✅ Ваше сообщение отправлено в поддержку. Ожидайте ответа.")
        clear_state(uid)
        return

    if action == "admin_dispute_reply":
        target_uid = int(data.get("user_id"))
        order_id = int(data.get("order_id"))
        try:
            bot.send_message(
                target_uid,
                "💬 <b>Ответ поддержки</b>\n"
                f"По заказу <b>#{order_id}</b>:\n\n"
                f"{safe_html(text)}"
            )
            bot.send_message(chat_id, "✅ Ответ отправлен пользователю.")
            log_event("admin_dispute_reply", user_id=uid, extra={"to_user_id": target_uid, "order_id": order_id})
        except Exception:
            bot.send_message(chat_id, GENERIC_ERROR_TEXT)
        clear_state(uid)
        return

    if action == "change_admin_password":
        cfg = get_config()
        cfg["admin_password"] = text
        save_config(cfg)
        clear_state(uid)
        send_clean(chat_id, "✅ Пароль обновлён.", reply_markup=admin_main_menu())
        return

    if action == "change_payment_phone":
        cfg = get_config()
        cfg["payment_phone"] = text
        save_config(cfg)
        clear_state(uid)
        send_clean(chat_id, "✅ Номер оплаты обновлён.", reply_markup=admin_main_menu())
        return

    if action == "change_manager_username":
        cfg = get_config()
        cfg["order_manager_username"] = text.replace("@", "").strip()
        save_config(cfg)
        clear_state(uid)
        send_clean(chat_id, "✅ Username поддержки обновлён.", reply_markup=admin_main_menu())
        return

    if action == "add_admin_by_id":
        try:
            new_id = int(text)
        except Exception:
            bot.send_message(chat_id, "❌ Введите корректный User ID числом.")
            return
        users = get_users()
        uid_str = str(new_id)
        users.setdefault(uid_str, {"username": "", "cart": [], "is_admin": False, "awaiting_payment_order_id": None})
        users[uid_str]["is_admin"] = True
        save_users(users)
        clear_state(uid)
        send_clean(chat_id, f"✅ Пользователь <code>{new_id}</code> теперь админ.", reply_markup=admin_main_menu())
        return

    if action == "remove_admin_by_id":
        try:
            rem_id = int(text)
        except Exception:
            bot.send_message(chat_id, "❌ Введите корректный User ID числом.")
            return
        users = get_users()
        uid_str = str(rem_id)
        if uid_str in users and users[uid_str].get("is_admin"):
            users[uid_str]["is_admin"] = False
            save_users(users)
            clear_state(uid)
            send_clean(chat_id, f"✅ Админ-права сняты с <code>{rem_id}</code>.", reply_markup=admin_main_menu())
        else:
            bot.send_message(chat_id, "❌ Этот ID не админ или не найден.")
        return

    if action == "admin_add_product" and step == 0:
        if len(text) < 2:
            bot.send_message(chat_id, "Введите название (минимум 2 символа):")
            return
        data["title"] = text
        set_state(uid, "admin_add_product", 1, data)
        bot.send_message(chat_id, "Введите категорию:")
        return

    if action == "admin_add_product" and step == 1:
        data["category"] = text
        set_state(uid, "admin_add_product", 2, data)
        bot.send_message(chat_id, "Введите тип (weapon или armor):")
        return

    if action == "admin_add_product" and step == 2:
        t = text.lower().strip()
        if t not in ("weapon", "armor"):
            bot.send_message(chat_id, "❌ Тип должен быть weapon или armor. Повторите:")
            return
        data["type"] = t
        set_state(uid, "admin_add_product", 3, data)
        bot.send_message(chat_id, "Введите цену (число):")
        return

    if action == "admin_add_product" and step == 3:
        try:
            price = int(text)
            if price < 0:
                raise ValueError()
        except Exception:
            bot.send_message(chat_id, "❌ Цена должна быть целым числом >= 0. Повторите:")
            return
        data["price"] = price
        set_state(uid, "admin_add_product", 4, data)
        bot.send_message(chat_id, "Введите описание (или напишите '-' чтобы пропустить):")
        return

    if action == "admin_add_product" and step == 4:
        desc = "" if text.strip() == "-" else normalize_description(text)
        products = get_products()
        pid = generate_product_id(products)
        products.append({
            "id": pid,
            "title": data["title"],
            "type": data["type"],
            "category": data.get("category", ""),
            "price": int(data["price"]),
            "description": desc
        })
        save_products(products)
        clear_state(uid)
        send_clean(chat_id, f"✅ Товар добавлен. ID: <code>{pid}</code>", reply_markup=admin_main_menu())
        return

    if action == "admin_add_escort" and step == 0:
        if len(text) < 2:
            bot.send_message(chat_id, "Введите название (минимум 2 символа):")
            return
        data["title"] = text
        set_state(uid, "admin_add_escort", 1, data)
        bot.send_message(chat_id, "Введите категорию:")
        return

    if action == "admin_add_escort" and step == 1:
        data["category"] = text
        set_state(uid, "admin_add_escort", 2, data)
        bot.send_message(chat_id, "Введите цену (число):")
        return

    if action == "admin_add_escort" and step == 2:
        try:
            price = int(text)
            if price < 0:
                raise ValueError()
        except Exception:
            bot.send_message(chat_id, "❌ Цена должна быть числом >=0. Повторите:")
            return
        data["price"] = price
        set_state(uid, "admin_add_escort", 3, data)
        bot.send_message(chat_id, "Введите описание (или '-' чтобы пропустить):")
        return

    if action == "admin_add_escort" and step == 3:
        desc = "" if text.strip() == "-" else normalize_description(text)
        products = get_products()
        pid = generate_product_id(products)
        products.append({
            "id": pid,
            "title": data["title"],
            "type": "escort",
            "category": data.get("category", ""),
            "price": int(data["price"]),
            "description": desc
        })
        save_products(products)
        clear_state(uid)
        send_clean(chat_id, f"✅ Сопровождение добавлено. ID: <code>{pid}</code>", reply_markup=admin_main_menu())
        return

    if action == "admin_delete_product":
        try:
            pid = int(text)
        except Exception:
            bot.send_message(chat_id, "❌ Введите ID числом:")
            return
        products = get_products()
        p = next((x for x in products if x.get("id") == pid and x.get("type") in ("weapon","armor")), None)
        if not p:
            bot.send_message(chat_id, "❌ Товар (weapon/armor) не найден.")
            return
        products = [x for x in products if x.get("id") != pid]
        save_products(products)
        clear_state(uid)
        send_clean(chat_id, f"✅ Товар ID <code>{pid}</code> удалён.", reply_markup=admin_main_menu())
        return

    if action == "admin_delete_escort":
        try:
            pid = int(text)
        except Exception:
            bot.send_message(chat_id, "❌ Введите ID числом:")
            return
        products = get_products()
        p = next((x for x in products if x.get("id") == pid and x.get("type") == "escort"), None)
        if not p:
            bot.send_message(chat_id, "❌ Сопровождение не найдено.")
            return
        products = [x for x in products if x.get("id") != pid]
        save_products(products)
        clear_state(uid)
        send_clean(chat_id, f"✅ Сопровождение ID <code>{pid}</code> удалено.", reply_markup=admin_main_menu())
        return

    if action == "admin_change_price" and step == 0:
        try:
            pid = int(text)
        except Exception:
            bot.send_message(chat_id, "❌ Введите ID числом:")
            return
        p = find_product_by_id(pid)
        if not p or p.get("type") not in ("weapon","armor"):
            bot.send_message(chat_id, "❌ Товар (weapon/armor) не найден.")
            return
        data["pid"] = pid
        set_state(uid, "admin_change_price", 1, data)
        bot.send_message(chat_id, f"Введите новую цену для ID {pid}:")
        return

    if action == "admin_change_price" and step == 1:
        try:
            price = int(text)
            if price < 0:
                raise ValueError()
        except Exception:
            bot.send_message(chat_id, "❌ Цена должна быть числом >=0. Повторите:")
            return
        pid = int(data["pid"])
        products = get_products()
        p = next((x for x in products if x.get("id") == pid), None)
        if not p:
            bot.send_message(chat_id, "❌ Товар не найден.")
            clear_state(uid)
            return
        p["price"] = price
        save_products(products)
        clear_state(uid)
        send_clean(chat_id, f"✅ Цена обновлена для ID <code>{pid}</code>: <b>{price} TMT</b>", reply_markup=admin_main_menu())
        return

    if action == "admin_change_escort_price" and step == 0:
        try:
            pid = int(text)
        except Exception:
            bot.send_message(chat_id, "❌ Введите ID числом:")
            return
        p = find_product_by_id(pid)
        if not p or p.get("type") != "escort":
            bot.send_message(chat_id, "❌ Сопровождение не найдено.")
            return
        data["pid"] = pid
        set_state(uid, "admin_change_escort_price", 1, data)
        bot.send_message(chat_id, f"Введите новую цену для ID {pid}:")
        return

    if action == "admin_change_escort_price" and step == 1:
        try:
            price = int(text)
            if price < 0:
                raise ValueError()
        except Exception:
            bot.send_message(chat_id, "❌ Цена должна быть числом >=0. Повторите:")
            return
        pid = int(data["pid"])
        products = get_products()
        p = next((x for x in products if x.get("id") == pid), None)
        if not p:
            bot.send_message(chat_id, "❌ Товар не найден.")
            clear_state(uid)
            return
        p["price"] = price
        save_products(products)
        clear_state(uid)
        send_clean(chat_id, f"✅ Цена сопровождения обновлена для ID <code>{pid}</code>: <b>{price} TMT</b>", reply_markup=admin_main_menu())
        return

    if action == "admin_change_desc" and step == 0:
        try:
            pid = int(text)
        except Exception:
            bot.send_message(chat_id, "❌ Введите ID числом:")
            return
        p = find_product_by_id(pid)
        if not p:
            bot.send_message(chat_id, "❌ Товар не найден.")
            return
        data["pid"] = pid
        set_state(uid, "admin_change_desc", 1, data)
        bot.send_message(chat_id, "Введите новое описание (или '-' чтобы очистить):")
        return

    if action == "admin_change_desc" and step == 1:
        pid = int(data["pid"])
        products = get_products()
        p = next((x for x in products if x.get("id") == pid), None)
        if not p:
            bot.send_message(chat_id, "❌ Товар не найден.")
            clear_state(uid)
            return
        p["description"] = "" if text.strip() == "-" else normalize_description(text)
        save_products(products)
        clear_state(uid)
        send_clean(chat_id, f"✅ Описание обновлено для ID <code>{pid}</code>.", reply_markup=admin_main_menu())
        return

    if action == "order_reject_reason":
        oid = int(data.get("order_id"))
        reason = text or "Оплата отклонена"
        reject_order(uid, oid, reason)
        clear_state(uid)
        bot.send_message(chat_id, f"✅ Заказ #{oid} отклонён.")
        return

    if action == "order_send_message":
        oid = int(data.get("order_id"))
        orders = get_orders()
        o = next((x for x in orders if x.get("id") == oid), None)
        if not o:
            bot.send_message(chat_id, "Заказ не найден.")
            clear_state(uid)
            return
        try:
            bot.send_message(o["user_id"], text)
            bot.send_message(chat_id, "✅ Сообщение отправлено.")
        except Exception:
            bot.send_message(chat_id, GENERIC_ERROR_TEXT)
        clear_state(uid)
        return

    if action == "ai_operator_full":
        raw, obj = ai_operator_plan(uid, text)
        if not obj:
            bot.send_message(chat_id, "Не удалось получить план. Попробуйте иначе.")
            return

        actions = obj.get("actions", [])
        summary = obj.get("summary", "")
        risk = (obj.get("risk") or "medium").lower()

        if not actions:
            bot.send_message(chat_id, f"Нужно уточнение:\n{safe_html(summary)}")
            return

        key = short_hash({"admin_id": uid, "ts": now_ts(), "actions": actions})
        pending_ai_actions[key] = {"admin_id": uid, "created_ts": now_ts(), "actions": actions, "summary": summary, "risk": risk}

        lines = ["🧰 <b>План</b>"]
        if risk == "high":
            lines.append("⚠️ <b>Опасно</b>: проверьте внимательно.")
        if summary:
            lines.append(f"\n<b>Summary:</b> {safe_html(summary)}")
        lines.append("\n<b>Actions:</b>")
        for i, a in enumerate(actions, 1):
            lines.append(f"{i}) <code>{a.get('type')}</code> {safe_html(json.dumps(a.get('params', {}), ensure_ascii=False))}")

        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("✅ Подтвердить", callback_data=f"ai_apply_{key}"))
        kb.add(types.InlineKeyboardButton("❌ Отказаться", callback_data=f"ai_deny_{key}"))
        bot.send_message(chat_id, "\n".join(lines), reply_markup=kb)
        return

    bot.send_message(chat_id, "Неизвестное состояние. Напишите «Отмена».")

# ================== CALLBACKS ==================

def admin_list_products_text(filter_type: Optional[str] = None) -> str:
    products = get_products()
    lines = ["<b>Товары:</b>"]
    for p in products:
        if filter_type and p.get("type") != filter_type:
            continue
        lines.append(f"• ID <code>{p.get('id')}</code> | {safe_html(p.get('title',''))} | {p.get('type')} | {p.get('price')} TMT")
    if len(lines) == 1:
        return "<b>Товары:</b>\n(пусто)"
    return "\n".join(lines[:200])

@bot.callback_query_handler(func=lambda c: True)
@cooldown_guard
def callback_handler(call):
    data = call.data
    chat_id = call.message.chat.id
    uid = call.from_user.id
    username = call.from_user.username
    get_or_create_user(uid, username)

    if data == "back_main_menu":
        send_clean(chat_id, "Главное меню:", reply_markup=main_menu())
        bot.answer_callback_query(call.id)
        return

    if data == "open_cart":
        show_cart(chat_id, uid)
        bot.answer_callback_query(call.id)
        return

    if data.startswith("shop_") and "_list_" in data:
        parts = data.split("_")
        if len(parts) < 4:
            bot.answer_callback_query(call.id, "Ошибка.", show_alert=True)
            return
        ptype = parts[1]
        try:
            page = int(parts[-1])
        except Exception:
            page = 0
        if ptype not in ("weapon", "armor", "escort"):
            bot.answer_callback_query(call.id, "Ошибка.", show_alert=True)
            return
        text, pages = render_products_list_text(ptype, page)
        kb = products_list_kb(ptype, page, pages)
        send_clean(chat_id, text, reply_markup=kb)
        bot.answer_callback_query(call.id)
        return

    if data.startswith("prod_open_"):
        parts = data.split("_")
        if len(parts) < 4:
            bot.answer_callback_query(call.id, "Ошибка.", show_alert=True)
            return
        ptype = parts[2]
        try:
            pid = int(parts[3])
        except Exception:
            bot.answer_callback_query(call.id, "Ошибка ID.", show_alert=True)
            return
        items = products_sorted_by_id(ptype)
        if not items:
            bot.answer_callback_query(call.id, "Пусто.", show_alert=True)
            return
        idx = find_index_by_id(items, pid)
        p = items[idx]
        text = render_product_page_text(p, idx, len(items), ptype)
        kb = product_page_kb(ptype, idx, len(items), int(p.get("id")))
        send_clean(chat_id, text, reply_markup=kb)
        bot.answer_callback_query(call.id)
        return

    if data.startswith("prod_nav_"):
        parts = data.split("_")
        if len(parts) < 4:
            bot.answer_callback_query(call.id, "Ошибка.", show_alert=True)
            return
        ptype = parts[2]
        try:
            idx = int(parts[3])
        except Exception:
            idx = 0
        items = products_sorted_by_id(ptype)
        if not items:
            bot.answer_callback_query(call.id, "Пусто.", show_alert=True)
            return
        idx = max(0, min(idx, len(items) - 1))
        p = items[idx]
        text = render_product_page_text(p, idx, len(items), ptype)
        kb = product_page_kb(ptype, idx, len(items), int(p.get("id")))
        send_clean(chat_id, text, reply_markup=kb)
        bot.answer_callback_query(call.id)
        return

    if data.startswith("prod_add_"):
        try:
            pid = int(data.split("_")[-1])
        except Exception:
            bot.answer_callback_query(call.id, "Ошибка ID.", show_alert=True)
            return
        p = find_product_by_id(pid)
        if not p:
            bot.answer_callback_query(call.id, "Товар не найден.", show_alert=True)
            return
        add_to_cart(uid, pid)
        bot.answer_callback_query(call.id, "✅ Добавлено в корзину.")
        return

    if data.startswith("shop_addbyid_"):
        ptype = data.split("_")[-1]
        if ptype not in ("weapon", "armor", "escort"):
            bot.answer_callback_query(call.id, "Ошибка.", show_alert=True)
            return
        set_state(uid, "user_add_to_cart_by_id", 0, {"ptype": ptype})
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "Введите ID товара, чтобы добавить в корзину (или «Отмена»):")
        return

    if data == "cart_clear":
        clear_cart(uid)
        send_clean(chat_id, "🧺 Корзина очищена.", reply_markup=main_menu())
        bot.answer_callback_query(call.id)
        return

    if data == "cart_checkout":
        items = get_cart_items(uid)
        if not items:
            bot.answer_callback_query(call.id, "Корзина пуста.", show_alert=True)
            return
        order = create_order_for_user(uid, username)
        if not order:
            bot.answer_callback_query(call.id, "Не удалось создать заказ.", show_alert=True)
            return

        clear_cart(uid)

        users = get_users()
        suid = str(uid)
        users.setdefault(suid, {"username": username or "", "cart": [], "is_admin": False, "awaiting_payment_order_id": None})
        users[suid]["awaiting_payment_order_id"] = order["id"]
        save_users(users)

        cfg = get_config()
        send_clean(
            chat_id,
            "✅ <b>Заказ оформлен!</b>\n\n"
            f"ID: <code>{order['id']}</code>\n"
            f"Сумма: <b>{order['total']} TMT</b>\n\n"
            f"Оплатите на номер: <b>{safe_html(cfg.get('payment_phone','не указан'))}</b>\n"
            "После оплаты отправьте фото сюда.",
            reply_markup=main_menu()
        )
        bot.answer_callback_query(call.id)
        return

    if data.startswith("dispute_menu_"):
        try:
            oid = int(data.split("_")[-1])
        except Exception:
            bot.answer_callback_query(call.id, "Ошибка.", show_alert=True)
            return

        kb = types.InlineKeyboardMarkup()
        url = support_url()
        if url:
            kb.add(types.InlineKeyboardButton("🆘 Написать в поддержку", url=url))
        kb.add(types.InlineKeyboardButton("✉️ Написать через бота", callback_data=f"dispute_msg_{oid}"))
        kb.add(types.InlineKeyboardButton("🏠 Главное меню", callback_data="back_main_menu"))

        bot.answer_callback_query(call.id)
        try:
            bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=kb)
        except Exception:
            pass
        bot.send_message(chat_id, f"⚖️ <b>Оспорить заказ #{oid}</b>\nВыберите способ:", reply_markup=kb)
        return

    if data.startswith("dispute_msg_"):
        try:
            oid = int(data.split("_")[-1])
        except Exception:
            bot.answer_callback_query(call.id, "Ошибка.", show_alert=True)
            return

        st = get_state(uid)
        if st and st.get("action") == "user_dispute_message" and int(st.get("data", {}).get("order_id", -1)) == oid:
            bot.answer_callback_query(call.id, "Вы уже в режиме ввода сообщения.")
            return

        set_state(uid, "user_dispute_message", 0, {"order_id": oid})
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, f"✉️ Напишите сообщение для поддержки по заказу <b>#{oid}</b> (или «Отмена»):")
        return

    if data.startswith("dispute_reply_") and is_admin(uid):
        parts = data.split("_")
        if len(parts) < 4:
            bot.answer_callback_query(call.id, "Ошибка.", show_alert=True)
            return
        try:
            order_id = int(parts[2])
            target_uid = int(parts[3])
        except Exception:
            bot.answer_callback_query(call.id, "Ошибка.", show_alert=True)
            return
        set_state(uid, "admin_dispute_reply", 0, {"order_id": order_id, "user_id": target_uid})
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, f"↩️ Введите ответ пользователю <code>{target_uid}</code> по заказу <b>#{order_id}</b> (или «Отмена»):")
        return

    if data == "admin_back_main":
        send_clean(chat_id, "Админ-панель:", reply_markup=admin_main_menu())
        bot.answer_callback_query(call.id)
        return

    if data == "admin_add_product" and is_admin(uid):
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "Введите название товара (или «Отмена»):")
        set_state(uid, "admin_add_product", 0, {})
        return

    if data == "admin_delete_product" and is_admin(uid):
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, admin_list_products_text())
        bot.send_message(chat_id, "Введите ID товара (weapon/armor) для удаления:")
        set_state(uid, "admin_delete_product", 0, {})
        return

    if data == "admin_change_price" and is_admin(uid):
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, admin_list_products_text())
        bot.send_message(chat_id, "Введите ID товара (weapon/armor) для изменения цены:")
        set_state(uid, "admin_change_price", 0, {})
        return

    if data == "admin_add_escort" and is_admin(uid):
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "Введите название сопровождения (или «Отмена»):")
        set_state(uid, "admin_add_escort", 0, {})
        return

    if data == "admin_delete_escort" and is_admin(uid):
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, admin_list_products_text("escort"))
        bot.send_message(chat_id, "Введите ID сопровождения для удаления:")
        set_state(uid, "admin_delete_escort", 0, {})
        return

    if data == "admin_change_escort_price" and is_admin(uid):
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, admin_list_products_text("escort"))
        bot.send_message(chat_id, "Введите ID сопровождения для изменения цены:")
        set_state(uid, "admin_change_escort_price", 0, {})
        return

    if data == "admin_change_desc" and is_admin(uid):
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, admin_list_products_text())
        bot.send_message(chat_id, "Введите ID товара для изменения описания:")
        set_state(uid, "admin_change_desc", 0, {})
        return

    if data == "admin_change_password" and is_admin(uid):
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "Введите новый пароль админки:")
        set_state(uid, "change_admin_password", 0, {})
        return

    if data == "admin_change_payment_phone" and is_admin(uid):
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "Введите новый номер оплаты:")
        set_state(uid, "change_payment_phone", 0, {})
        return

    if data == "admin_change_manager_username" and is_admin(uid):
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "Введите новый username поддержки (без @):")
        set_state(uid, "change_manager_username", 0, {})
        return

    if data == "admin_clear_orders" and is_admin(uid):
        save_orders([])
        log_event("admin_clear_orders", user_id=uid)
        bot.answer_callback_query(call.id, "✅ Заказы очищены.", show_alert=True)
        return

    if data == "admin_restart_bot" and is_admin(uid):
        bot.answer_callback_query(call.id, "Перезапуск...")
        bot.send_message(chat_id, "🔄 Перезапуск бота...")
        time.sleep(0.5)
        restart_self()
        return

    if data == "admin_add_admin" and is_admin(uid):
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "Введите User ID для добавления в админы:")
        set_state(uid, "add_admin_by_id", 0, {})
        return

    if data == "admin_remove_admin" and is_admin(uid):
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "Введите User ID для удаления из админов:")
        set_state(uid, "remove_admin_by_id", 0, {})
        return

    if data.startswith("admin_logs_") and is_admin(uid):
        now = now_ts()
        if data == "admin_logs_1h":
            logs = [l for l in get_logs() if now - 3600 <= int(l.get("timestamp", 0)) <= now]
        elif data == "admin_logs_24h":
            logs = [l for l in get_logs() if now - 86400 <= int(l.get("timestamp", 0)) <= now]
        elif data == "admin_logs_7d":
            logs = [l for l in get_logs() if now - 7*86400 <= int(l.get("timestamp", 0)) <= now]
        else:
            logs = get_logs()

        if not logs:
            bot.send_message(chat_id, "Логи отсутствуют.")
        else:
            lines = ["📜 <b>Логи (последние 50)</b>:"]
            for l in logs[-50:]:
                ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(l.get("timestamp", 0))))
                lines.append(f"{ts} | {l.get('type')} | user_id={l.get('user_id')}")
            bot.send_message(chat_id, "\n".join(lines))
        bot.answer_callback_query(call.id)
        return

    if data == "ai_info_scout" and is_admin(uid):
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "Vision-модель: <b>meta-llama/llama-4-scout-17b-16e-instruct</b>")
        return

    if data == "ai_operator_full" and is_admin(uid):
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "🧰 ИИ‑оператор: напишите задачу. Для выхода — «Отмена».")
        set_state(uid, "ai_operator_full", 0, {})
        return

    if data.startswith("ai_apply_") or data.startswith("ai_deny_"):
        key = data.split("_", 2)[-1]
        rec = pending_ai_actions.get(key)
        if not rec:
            bot.answer_callback_query(call.id, "План устарел.", show_alert=True)
            return
        if rec.get("admin_id") != uid:
            bot.answer_callback_query(call.id, "Это не ваш план.", show_alert=True)
            return

        if data.startswith("ai_deny_"):
            pending_ai_actions.pop(key, None)
            bot.answer_callback_query(call.id, "Отклонено.")
            bot.send_message(chat_id, "❌ План отклонён.")
            return

        bot.answer_callback_query(call.id, "Выполняю...")
        results = []
        restart_needed = False
        for a in rec.get("actions", []):
            ok, msg, r = execute_operator_action(uid, a)
            results.append({"ok": ok, "type": a.get("type"), "msg": msg})
            restart_needed = restart_needed or r

        pending_ai_actions.pop(key, None)
        bot.send_message(chat_id, "<b>Результаты:</b>\n<pre>" + safe_html(json.dumps(results, ensure_ascii=False, indent=2)) + "</pre>")

        if restart_needed:
            bot.send_message(chat_id, "🔄 Перезапуск через 1 секунду...")
            time.sleep(1)
            restart_self()
        return

    if data.startswith("check_payment_") or data.startswith("check_payment_deep_"):
        deep = data.startswith("check_payment_deep_")
        try:
            order_id = int(data.split("_")[-1])
        except Exception:
            bot.answer_callback_query(call.id, "Некорректный ID.", show_alert=True)
            return

        orders = get_orders()
        order = next((o for o in orders if o.get("id") == order_id), None)
        if not order:
            bot.answer_callback_query(call.id, "Заказ не найден.", show_alert=True)
            return
        if not order.get("payment_photo_file_id"):
            bot.answer_callback_query(call.id, "Фото отсутствует.", show_alert=True)
            return

        try:
            file_info = bot.get_file(order["payment_photo_file_id"])
            file_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_info.file_path}"
        except Exception:
            bot.answer_callback_query(call.id, "Ошибка получения фото.", show_alert=True)
            return

        bot.answer_callback_query(call.id, "ИИ анализирует...")

        kb = types.InlineKeyboardMarkup()
        if order.get("username"):
            kb.add(types.InlineKeyboardButton("💬 Перейти в чат", url=user_chat_url(order["username"])))
        kb.add(types.InlineKeyboardButton("✉️ Отправить сообщение", callback_data=f"order_msg_{order_id}"))
        kb.add(types.InlineKeyboardButton("❌ Заказ отклонён", callback_data=f"order_reject_{order_id}"))

        try:
            bot.send_photo(
                chat_id,
                order["payment_photo_file_id"],
                caption=(
                    f"📦 Заказ <b>#{order_id}</b>\n"
                    f"Пользователь: <code>{order.get('user_id')}</code> @{order.get('username') or 'нет'}\n"
                    f"Сумма: <b>{order.get('total')} TMT</b>\n"
                    f"Режим: <b>{'подробный' if deep else 'обычный'}</b>"
                ),
                reply_markup=kb
            )
        except Exception:
            pass

        verdict = ai_check_payment_image(order, file_url, deep=deep)
        order["ai_verdict_last"] = verdict
        order["ai_verdict_last_ts"] = now_ts()
        save_orders(orders)

        bot.send_message(chat_id, f"🧠 <b>Проверка по заказу #{order_id}</b>:\n\n{verdict}")
        return

    if data.startswith("order_reject_") and is_admin(uid):
        try:
            oid = int(data.split("_")[-1])
        except Exception:
            bot.answer_callback_query(call.id, "ID неверный.", show_alert=True)
            return
        set_state(uid, "order_reject_reason", 0, {"order_id": oid})
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, f"Введите причину отклонения заказа <b>#{oid}</b> (или «Отмена»):")
        return

    if data.startswith("order_msg_") and is_admin(uid):
        try:
            oid = int(data.split("_")[-1])
        except Exception:
            bot.answer_callback_query(call.id, "ID неверный.", show_alert=True)
            return
        set_state(uid, "order_send_message", 0, {"order_id": oid})
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, f"Введите сообщение клиенту по заказу <b>#{oid}</b> (или «Отмена»):")
        return

    bot.answer_callback_query(call.id)

# ================== MAIN LOOP (no crash) ==================

app = Flask(__name__)

@app.get("/")
def root():
    return "OK", 200

@app.get("/health")
def health():
    return "OK", 200

def _run_polling_forever():
    ensure_files()
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=60)
        except Exception as e:
            log_error("infinity_polling", e)
            time.sleep(2)

if __name__ == "__main__":
    t = threading.Thread(target=_run_polling_forever, daemon=True)
    t.start()
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)

