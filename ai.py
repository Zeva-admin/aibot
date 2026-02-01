import telebot
from telebot import types

# Вставь сюда токен своего бота
TOKEN = "8288661704:AAGqMezt0_iEzQfVM3eJxqAd87Ihakucg3o"
bot = telebot.TeleBot(TOKEN)

# Ссылка на чат
CHAT_LINK = "https://t.me/+kdsSZ-vh0943MDFi"

# Юзеры со-руководителей
LEADERS = [("Андрей", "https://t.me/keika2035"),
           ("Aboo", "https://t.me/G_U_G_A_1")]

# Главное меню
def main_menu(name):
    markup = types.InlineKeyboardMarkup(row_width=1)
    btn1 = types.InlineKeyboardButton("💬 Вступить в чат", callback_data="join_chat")
    btn2 = types.InlineKeyboardButton("📞 Связаться с со-руководителями", callback_data="leaders")
    markup.add(btn1, btn2)
    return markup

# Кнопка назад (всегда внизу)
def back_button():
    markup = types.InlineKeyboardMarkup(row_width=1)
    btn_back = types.InlineKeyboardButton("⬅️ Назад в меню", callback_data="back")
    markup.add(btn_back)
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
        btn_link = types.InlineKeyboardButton("🔗 Перейти в чат", url=CHAT_LINK)
        btn_back = types.InlineKeyboardButton("⬅️ Назад в меню", callback_data="back")
        markup.add(btn_link, btn_back)
        bot.edit_message_text(chat_id=call.message.chat.id,
                              message_id=call.message.message_id,
                              text=(
                                  "━━━━━━━━━━━━━━━\n"
                                  "💬 Наш чат ждёт тебя:\n"
                                  "━━━━━━━━━━━━━━━"
                              ),
                              reply_markup=markup)

    elif call.data == "leaders":
        markup = types.InlineKeyboardMarkup(row_width=1)
        for name, url in LEADERS:
            markup.add(types.InlineKeyboardButton(f"👤 {name}", url=url))
        btn_back = types.InlineKeyboardButton("⬅️ Назад в меню", callback_data="back")
        markup.add(btn_back)
        bot.edit_message_text(chat_id=call.message.chat.id,
                              message_id=call.message.message_id,
                              text=(
                                  "━━━━━━━━━━━━━━━\n"
                                  "📞 Связаться можно с со‑руководителями:\n"
                                  "━━━━━━━━━━━━━━━"
                              ),
                              reply_markup=markup)

    elif call.data == "back":
        name = call.from_user.first_name
        bot.edit_message_text(chat_id=call.message.chat.id,
                              message_id=call.message.message_id,
                              text=(
                                  "━━━━━━━━━━━━━━━\n"
                                  "🏠 Главное меню:\n"
                                  "━━━━━━━━━━━━━━━"
                              ),
                              reply_markup=main_menu(name))

print("Бот запущен...")
bot.polling()
