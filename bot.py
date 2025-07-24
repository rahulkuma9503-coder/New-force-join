import os
import logging
from threading import Thread
from flask import Flask, request
from pymongo import MongoClient
from telegram import Update, ChatPermissions
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters
)

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# MongoDB setup
mongo_client = MongoClient(os.getenv('MONGO_URI'))
db = mongo_client.telegram_bot
fsub_collection = db.fsub_channels

# Flask app for health checks
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Bot is running", 200

def run_flask():
    app.run(host='0.0.0.0', port=8000)

# Telegram bot handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == 'private':
        await update.message.reply_text(
            "Hi! I'm a forced subscription bot. Add me to a group and use /fsub to set a channel."
        )
    else:
        await update.message.reply_text(
            "I'm a forced subscription bot. Use /fsub to set a required channel for this group."
        )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Commands:\n"
        "/start - Introduction\n"
        "/help - This message\n"
        "/fsub @channel - Set forced subscription channel (admins only)\n\n"
        "I'll mute anyone who hasn't joined the required channel."
    )

async def set_fsub_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    
    # Check if command is used in a group
    if chat.type == 'private':
        await update.message.reply_text("This command only works in groups.")
        return
    
    # Check if user is admin
    member = await chat.get_member(user.id)
    if member.status not in ['administrator', 'creator']:
        await update.message.reply_text("Only admins can use this command.")
        return
    
    # Check if channel is mentioned
    if not context.args or not context.args[0].startswith('@'):
        await update.message.reply_text("Usage: /fsub @channelusername")
        return
    
    channel = context.args[0].lstrip('@')
    
    # Save to MongoDB
    fsub_collection.update_one(
        {'chat_id': chat.id},
        {'$set': {'channel': channel}},
        upsert=True
    )
    
    await update.message.reply_text(
        f"✅ Success! All members must now join @{channel} to participate here."
    )

async def check_membership(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    
    # Skip if private chat or message from bot
    if chat.type == 'private' or user.is_bot:
        return
    
    # Check if group has a forced channel
    fsub_data = fsub_collection.find_one({'chat_id': chat.id})
    if not fsub_data:
        return
    
    channel = fsub_data['channel']
    
    try:
        # Check if user is admin (admins are exempt)
        member = await chat.get_member(user.id)
        if member.status in ['administrator', 'creator']:
            return
        
        # Check if user is in the channel
        chat_member = await context.bot.get_chat_member(f"@{channel}", user.id)
        if chat_member.status in ['left', 'kicked']:
            # Mute the user
            permissions = ChatPermissions(
                can_send_messages=False,
                can_send_media_messages=False,
                can_send_other_messages=False,
                can_add_web_page_previews=False
            )
            await chat.restrict_member(user.id, permissions)
            
            # Send warning message
            await update.message.reply_text(
                f"⚠️ {user.mention_html()} has been muted because they haven't joined @{channel}.\n"
                "Join the channel and contact an admin to be unmuted.",
                parse_mode='HTML'
            )
    except Exception as e:
        logger.error(f"Error checking membership: {e}")
        await update.message.reply_text(
            "⚠️ Error checking channel membership. Make sure I'm admin in the channel."
        )

def main():
    # Start Flask server in a separate thread
    Thread(target=run_flask, daemon=True).start()
    
    # Create Telegram bot
    application = ApplicationBuilder().token(os.getenv('BOT_TOKEN')).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("fsub", set_fsub_channel))
    application.add_handler(
        MessageHandler(filters.ChatType.GROUPS & ~filters.StatusUpdate.ALL, check_membership)
    )
    
    # Start polling
    application.run_polling()

if __name__ == '__main__':
    main()
