import os
from flask import Flask
from threading import Thread
from dotenv import load_dotenv
from telegram import Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)
from pymongo import MongoClient

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

# MongoDB setup
client = MongoClient(MONGO_URI)
db = client["telegram_bot"]
fsub_collection = db["fsub_settings"]

# Flask app
app = Flask(__name__)

@app.route('/')
def index():
    return "🤖 Join Forces Bot (MongoDB) is Running!"

# Commands
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Welcome! Use /help to see commands.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📌 Bot Commands:\n"
        "/start - Welcome message\n"
        "/help - Show commands\n"
        "/fsub @channel - Require users to join this channel before chatting"
    )

async def fsub_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user

    if not chat.type.endswith("group"):
        await update.message.reply_text("⚠️ This command only works in groups.")
        return

    member = await context.bot.get_chat_member(chat.id, user.id)
    if not member.status in ["administrator", "creator"]:
        await update.message.reply_text("❌ Only admins can use this command.")
        return

    if not context.args:
        await update.message.reply_text("❌ Usage: /fsub @channelusername")
        return

    channel = context.args[0]
    fsub_collection.update_one(
        {"group_id": chat.id},
        {"$set": {"required_channel": channel}},
        upsert=True
    )
    await update.message.reply_text(f"✅ Force-subscription enabled for {channel}.")

# Auto mute
async def restrict_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    data = fsub_collection.find_one({"group_id": chat_id})
    if not data:
        return

    required_channel = data["required_channel"]
    try:
        member = await context.bot.get_chat_member(required_channel, user.id)
        if member.status in ["left", "kicked"]:
            await context.bot.restrict_chat_member(
                chat_id,
                user.id,
                ChatPermissions(can_send_messages=False)
            )
            join_button = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Join Channel", url=f"https://t.me/{required_channel.lstrip('@')}")]
            ])
            await update.message.reply_text(
                f"🚫 You must join {required_channel} to chat.",
                reply_markup=join_button
            )
    except Exception as e:
        print("❌ Restriction error:", e)

# Bot runner
def run_bot():
    print("🟢 Starting Telegram bot...")
    app_bot = ApplicationBuilder().token(BOT_TOKEN).build()
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("help", help_command))
    app_bot.add_handler(CommandHandler("fsub", fsub_command))
    app_bot.add_handler(MessageHandler(filters.TEXT & filters.Group(), restrict_user))
    print("🤖 Telegram Bot is now polling...")
    app_bot.run_polling()

# Web runner
def run_web():
    app.run(host="0.0.0.0", port=8000)

# Entry
if __name__ == "__main__":
    Thread(target=run_bot).start()
    Thread(target=run_web).start()
    
