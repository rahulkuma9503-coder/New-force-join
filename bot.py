import os, logging
from flask import Flask
from threading import Thread
from dotenv import load_dotenv
from telegram import Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from pymongo import MongoClient, errors

# Enable logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

# MongoDB setup
try:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    client.server_info()  # force connect
    db = client["telegram_bot"]
    fsub = db["fsub_settings"]
    logger.info("✅ Connected to MongoDB")
except errors.ServerSelectionTimeoutError as e:
    logger.error("❌ MongoDB connection failed", exc_info=e)
    fsub = None

# Flask
app = Flask(__name__)
@app.route('/')
def index():
    return "🚀 Bot is running with logging"

# Command handlers with logging
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"Received /start from {update.effective_user.id}")
    await update.message.reply_text("👋 Bot is live! Use /help")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"Received /help from {update.effective_user.id}")
    await update.message.reply_text("📌 Commands: /start, /help, /fsub @channel")

async def fsub_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"Received /fsub {context.args} from {update.effective_user.id} in chat {update.effective_chat.id}")
    chat = update.effective_chat
    if not chat.type.endswith("group"):
        await update.message.reply_text("⚠️ Only works in groups")
        return
    member = await context.bot.get_chat_member(chat.id, update.effective_user.id)
    if member.status not in ["administrator", "creator"]:
        await update.message.reply_text("❌ Only admins")
        return
    if not context.args:
        await update.message.reply_text("❌ Usage: /fsub @channel")
        return
    channel = context.args[0]
    res = fsub.update_one({"group_id": chat.id}, {"$set": {"required_channel": channel}}, upsert=True)
    logger.info(f"Mongo update result: {res.raw_result}")
    await update.message.reply_text(f"✅ Force-sub set to {channel}")

async def restrict(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    logger.info(f"Message from {user.id} in group {chat_id}")
    rec = fsub.find_one({"group_id": chat_id})
    if not rec:
        logger.info("No fsub record, skipping")
        return
    logger.info(f"Found record: {rec}")
    try:
        cm = await context.bot.get_chat_member(rec["required_channel"], user.id)
        if cm.status in ["left", "kicked"]:
            logger.info(f"User {user.id} not in channel {rec['required_channel']} – muting")
            await context.bot.restrict_chat_member(chat_id, user.id, ChatPermissions(can_send_messages=False))
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Join", url=f"https://t.me/{rec['required_channel'].lstrip('@')}")]])
            await update.message.reply_text("🚫 Join the channel to chat", reply_markup=kb)
    except Exception as e:
        logger.error("Error in restrict_user", exc_info=e)

def run_bot():
    logger.info("🟢 Starting Telegram bot...")
    app_bot = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("help", help_command))
    app_bot.add_handler(CommandHandler("fsub", fsub_command))

    # 🔥 FIXED: restrict handler only for group messages
    app_bot.add_handler(MessageHandler(filters.TEXT & filters.ChatType.GROUPS, restrict))

    logger.info("🤖 Polling...")
    app_bot.run_polling()
    

def run_web():
    app.run(host="0.0.0.0", port=8000)

if __name__ == "__main__":
    Thread(target=run_bot).start()
    Thread(target=run_web).start()
    
