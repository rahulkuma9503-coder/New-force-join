import os
from telegram import Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from flask import Flask
from threading import Thread
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
REQUIRED_CHANNEL = os.getenv("REQUIRED_CHANNEL")  # example: @mychannelusername

app = Flask(__name__)

@app.route("/")
def home():
    return "Join Forces Bot Running"

# 🚫 Mute user if not in required channel
async def restrict_if_not_joined(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id

    try:
        status = await context.bot.get_chat_member(REQUIRED_CHANNEL, user.id)
        if status.status in ["left", "kicked"]:
            await context.bot.restrict_chat_member(
                chat_id,
                user.id,
                ChatPermissions(can_send_messages=False)
            )
            join_btn = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Join Channel", url=f"https://t.me/{REQUIRED_CHANNEL.replace('@','')}")]
            ])
            await update.message.reply_text(
                f"🚫 You must join {REQUIRED_CHANNEL} to talk here.",
                reply_markup=join_btn
            )
    except Exception as e:
        print(f"Error checking member status: {e}")

# 🆘 Help command
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Available Commands:\n"
        "/start - Start the bot\n"
        "/help - Show this message\n"
        "Bot will mute users who haven't joined the required channel."
    )

# ✅ Start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Welcome to Join Forces Bot!")

def start_bot():
    app_telegram = ApplicationBuilder().token(TOKEN).build()
    app_telegram.add_handler(CommandHandler("start", start))
    app_telegram.add_handler(CommandHandler("help", help_command))
    app_telegram.add_handler(MessageHandler(filters.TEXT & filters.Group(), restrict_if_not_joined))
    app_telegram.run_polling()

def run_web():
    app.run(host="0.0.0.0", port=8000)

if __name__ == "__main__":
    Thread(target=start_bot).start()
    Thread(target=run_web).start()
