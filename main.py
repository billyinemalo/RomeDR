import os
import sys
import json
import time
import logging
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta

from dotenv import load_dotenv
import telegram
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InputMediaPhoto,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

# ----------------- ЛОГИ/ENV -----------------
logging.basicConfig(level=logging.INFO)
logging.info(f"PTB_RUNTIME {telegram.__version__} | PY_RUNTIME {sys.version}")

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    logging.error("BOT_TOKEN не найден в .env")

# !!!! УКАЖИ ТУТ Telegram ID Ромы (как строку) !!!!
ROMA_ID = os.getenv("ROMA_ID", "123456789")  # <-- замени 123... на реальный ID или поставь в .env

# (необязательно) организатор/админ для обратной связи
ORGANIZER_ID = os.getenv("ORGANIZER_ID", "")  # можно пустым оставить

# ----------------- МЕНЮ -----------------
menu = ReplyKeyboardMarkup([
    [KeyboardButton("🎉 С днём рождения!")],
    [KeyboardButton("✨ Всего наилучшего")],
    [KeyboardButton("🕵️ Секретное поздравление"), KeyboardButton("✍️ Своё поздравление")],
    [KeyboardButton("🖼 Открытка"), KeyboardButton("📞 Написать организатору")],
    [KeyboardButton("🔁 Перезапустить бота")],
], resize_keyboard=True)

# ----------------- ПАМЯТЬ -----------------
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)
STATE_FILE = os.path.join(DATA_DIR, "state.json")

DEFAULT_STATE = {
    "last_secret": {}  # user_id -> ISO timestamp (чтобы не спамили секретной кнопкой)
}

def load_state():
    if not os.path.exists(STATE_FILE):
        save_state(DEFAULT_STATE)
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return DEFAULT_STATE.copy()

def save_state(s):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
    except Exception:
        logging.exception("Не смог сохранить state.json")

STATE = load_state()

# ----------------- HEALTHCHECK для Render -----------------
def start_health_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
        def log_message(self, *_): pass

    port = int(os.environ.get("PORT", "10000"))
    srv = HTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    logging.info(f"Healthcheck server started on :{port}")

# ----------------- УТИЛИТЫ -----------------
def pretty_sender(u: telegram.User) -> str:
    fn = u.first_name or ""
    ln = u.last_name or ""
    uname = f"@{u.username}" if u.username else ""
    full = (fn + " " + ln).strip() or "Без имени"
    parts = [full]
    if uname: parts.append(uname)
    parts.append(f"id:{u.id}")
    return " / ".join(parts)

async def send_to_roma(context: ContextTypes.DEFAULT_TYPE, text: str):
    try:
        await context.bot.send_message(chat_id=ROMA_ID, text=text)
        return True
    except Exception:
        logging.exception("Не удалось отправить Роме")
        return False

# ----------------- /start -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Это бот-поздравлятор 🎂\n"
        "Выбери кнопку и я передам поздравление Роме Гурко от твоего имени.",
        reply_markup=menu
    )

async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

# ----------------- КНОПКИ ПОЗДРАВЛЕНИЙ -----------------
async def wish_hb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    text = f"🎉 С днём рождения!\n— от {pretty_sender(u)}"
    ok = await send_to_roma(context, text)
    if ok:
        await update.message.reply_text("Готово! Передал Роме 🎈", reply_markup=menu)
    else:
        await update.message.reply_text("Не удалось отправить. Попробуй ещё раз позже 🙏", reply_markup=menu)

async def wish_best(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    text = (
        "✨ Поздравляю! Желаю крепкого здоровья, вдохновения, классных проектов "
        "и много поводов для радости! \n— от " + pretty_sender(u)
    )
    ok = await send_to_roma(context, text)
    if ok:
        await update.message.reply_text("Передал пожелания! ✨", reply_markup=menu)
    else:
        await update.message.reply_text("Не получилось отправить 😕", reply_markup=menu)

# СЕКРЕТ: 50 сообщений «С днём рождения»
SECRET_COOLDOWN = timedelta(hours=1)  # антиспам: раз в час

async def wish_secret(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    uid = str(u.id)
    now = datetime.utcnow()

    # антиспам
    ts_str = STATE["last_secret"].get(uid)
    if ts_str:
        try:
            last = datetime.fromisoformat(ts_str)
            if now - last < SECRET_COOLDOWN:
                left = SECRET_COOLDOWN - (now - last)
                mins = int(left.total_seconds() // 60)
                await update.message.reply_text(
                    f"Секретное можно раз в час. Подожди ещё ~{mins} мин ⏳",
                    reply_markup=menu
                )
                return
        except Exception:
            pass

    await update.message.reply_text("Запускаю секретное поздравление… 🕵️ Подожди немного.")

    # отправляем 50 раз, чтобы не словить flood — пауза
    sent = 0
    for i in range(50):
        ok = await send_to_roma(context, f"🎉 С днём рождения! — от {pretty_sender(u)} (секрет #{i+1}/50)")
        if ok:
            sent += 1
        await asyncio.sleep(0.25)  # щадящая задержка

    STATE["last_secret"][uid] = now.isoformat()
    save_state(STATE)

    await update.message.reply_text(f"Готово! Отправлено сообщений: {sent}/50 ✅", reply_markup=menu)

# СВОЁ ПОЗДРАВЛЕНИЕ (короткий диалог)
ASK_CUSTOM = 1

async def custom_wish_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Напиши здесь своё поздравление, я перешлю его Роме от твоего имени ✍️")
    return ASK_CUSTOM

async def custom_wish_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    txt = (update.message.text or "").strip()
    if not txt:
        await update.message.reply_text("Кажется, текста нет. Попробуй ещё раз 🙏", reply_markup=menu)
        return ConversationHandler.END

    payload = f"🎁 Поздравление от {pretty_sender(u)}:\n\n{txt}"
    ok = await send_to_roma(context, payload)
    if ok:
        await update.message.reply_text("Отправил! 🎁", reply_markup=menu)
    else:
        await update.message.reply_text("Хм, не получилось отправить. Повтори позже.", reply_markup=menu)
    return ConversationHandler.END

# ОТКРЫТКА (милый бонус)
CARD_URLS = [
    # можешь заменить на свои ссылки (фото/гиф/стикеры как фото)
    "https://i.imgur.com/jVx0y9N.jpg",
    "https://i.imgur.com/9Xj4QmT.jpg",
]

async def send_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if len(CARD_URLS) == 1:
            await context.bot.send_photo(update.effective_chat.id, CARD_URLS[0], caption="Открытка для Ромы 🎂")
        else:
            media = [InputMediaPhoto(u) for u in CARD_URLS[:10]]
            await context.bot.send_media_group(update.effective_chat.id, media=media)
            await context.bot.send_message(update.effective_chat.id, "Выбери понравившуюся и жми на поздравления!")
    except Exception:
        await update.message.reply_text("Не получилось загрузить открытку, попробуй позже 🙈")

# ОБРАТНАЯ СВЯЗЬ ОРГАНИЗАТОРУ
async def contact_org(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ORGANIZER_ID:
        await update.message.reply_text("Организатор пока недоступен 😊", reply_markup=menu)
        return
    u = update.effective_user
    me = pretty_sender(u)
    await update.message.reply_text("Передал организатору. Спасибо!")
    try:
        await context.bot.send_message(ORGANIZER_ID, f"✉️ Сообщение от {me}: хочет пообщаться по поводу бота-поздравлятора.")
    except Exception:
        pass

# ----------------- РОУТЕР КНОПОК -----------------
async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()
    if txt == "🎉 С днём рождения!":
        return await wish_hb(update, context)
    if txt == "✨ Всего наилучшего":
        return await wish_best(update, context)
    if txt == "🕵️ Секретное поздравление":
        return await wish_secret(update, context)
    if txt == "✍️ Своё поздравление":
        return await custom_wish_start(update, context)
    if txt == "🖼 Открытка":
        return await send_card(update, context)
    if txt == "📞 Написать организатору":
        return await contact_org(update, context)
    if txt == "🔁 Перезапустить бота":
        return await restart(update, context)

# ----------------- ОБРАБОТКА ОШИБОК -----------------
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.exception("Unhandled exception", exc_info=context.error)
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text("Ой! Что-то сломалось. Нажми «🔁 Перезапустить бота» 🙂", reply_markup=menu)
    except Exception:
        pass

# ----------------- ЗАПУСК -----------------
if __name__ == "__main__":
    start_health_server()

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_error_handler(on_error)

    # /start
    app.add_handler(CommandHandler("start", start))

    # Короткий диалог «своё поздравление»
    custom_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & filters.Regex("^✍️ Своё поздравление$"), custom_wish_start)],
        states={
            ASK_CUSTOM: [MessageHandler(filters.TEXT & ~filters.COMMAND, custom_wish_send)]
        },
        fallbacks=[],
    )
    app.add_handler(custom_conv)

    # Роутер остальных кнопок
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_buttons))

    app.run_polling(drop_pending_updates=True)
