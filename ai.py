import telebot
from telebot import types
import time

# Токен бота
TOKEN = "8288661704:AAH2FFO0NbU9FULEJ8MwvPAv7KYSSDMQtSQ"
bot = telebot.TeleBot(TOKEN, parse_mode="HTML")

# Ссылка на чат
CHAT_LINK = "https://t.me/+kdsSZ-vh0943MDFi"

# Со-руководители
LEADERS = [
    ("Андрей", "https://t.me/keika2035"),
    ("Aboo", "https://t.me/G_U_G_A_1")
]

# Главное меню
def main_menu(name):
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("💬 Вступить в чат", callback_data="join_chat"),
        types.InlineKeyboardButton("📞 Связаться с со-руководителями", callback_data="leaders")
    )
    return markup

# Кнопка назад
def back_button():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton("⬅️ Назад в меню", callback_data="back"))
    return markup

@bot.message_handler(commands=['start'])
def start(message):
    name = message.from_user.first_name
    text = (
        "━━━━━━━━━━━━━━━\n"
        f"🌟 Добро пожаловать, {name}!\n"
        "⚔️ Переходник клана В.К.Л.\n"
        "━━━━━━━━━━━━━━━\n\n"
        "Выберите действие ниже 👇"
    )
    bot.send_message(message.chat.id, text, reply_markup=main_menu(name))

@bot.callback_query_handler(func=lambda call: True)
def callback(call):
    if call.data == "join_chat":
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("🔗 Перейти в чат", url=CHAT_LINK),
            types.InlineKeyboardButton("⬅️ Назад в меню", callback_data="back")
        )
        bot.edit_message_text(
            "━━━━━━━━━━━━━━━\n"
            "💬 Наш чат ждёт тебя:\n"
            "━━━━━━━━━━━━━━━",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=markup
        )

    elif call.data == "leaders":
        markup = types.InlineKeyboardMarkup(row_width=1)
        for name, url in LEADERS:
            markup.add(types.InlineKeyboardButton(f"👤 {name}", url=url))
        markup.add(types.InlineKeyboardButton("⬅️ Назад в меню", callback_data="back"))

        bot.edit_message_text(
            "━━━━━━━━━━━━━━━\n"
            "📞 Со‑руководители клана:\n"
            "━━━━━━━━━━━━━━━",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=markup
        )

    elif call.data == "back":
        name = call.from_user.first_name
        bot.edit_message_text(
            "━━━━━━━━━━━━━━━\n"
            "🏠 Главное меню:\n"
            "━━━━━━━━━━━━━━━",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=main_menu(name)
        )

# Надёжный запуск для bothost.ru
while True:
    try:
        bot.infinity_polling(skip_pending=True, timeout=60)
    except Exception as e:
        print("Ошибка:", e)
        time.sleep(3)
