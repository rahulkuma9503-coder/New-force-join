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
        "/fsub [@channel|channel_id|reply to channel msg] - Set forced subscription channel (admins only)\n\n"
        "I'll mute anyone who hasn't joined the required channel."
    )

async def set_fsub_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    
    if chat.type == 'private':
        await update.message.reply_text("This command only works in groups.")
        return
    
    member = await chat.get_member(user.id)
    if member.status not in ['administrator', 'creator']:
        await update.message.reply_text("Only admins can use this command.")
        return
    
    # Check if replying to a channel message
    if update.message.reply_to_message and update.message.reply_to_message.sender_chat:
        if update.message.reply_to_message.sender_chat.type == 'channel':
            channel = update.message.reply_to_message.sender_chat.username or str(update.message.reply_to_message.sender_chat.id)
            await save_fsub_channel(chat.id, channel, update)
            return
    
    # Check for channel ID or @username in arguments
    if context.args:
        channel_input = context.args[0]
        if channel_input.startswith('@'):
            channel = channel_input[1:]
        elif channel_input.isdigit() or (channel_input.startswith('-') and channel_input[1:].isdigit()):
            channel = channel_input
        else:
            await update.message.reply_text("Invalid channel format. Use @username, channel ID, or reply to a channel message.")
            return
        
        await save_fsub_channel(chat.id, channel, update)
    else:
        await update.message.reply_text(
            "Usage:\n"
            "/fsub @channelusername\n"
            "/fsub channel_id\n"
            "Or reply to a channel message with /fsub"
        )

async def save_fsub_channel(chat_id: int, channel: str, update: Update):
    try:
        # Verify the channel exists
        chat = await update.get_bot().get_chat(f"@{channel}" if not channel.startswith('-') else channel)
        if chat.type != 'channel':
            await update.message.reply_text("The specified chat is not a channel.")
            return
        
        # Save to MongoDB
        fsub_collection.update_one(
            {'chat_id': chat_id},
            {'$set': {'channel': channel, 'channel_id': chat.id}},
            upsert=True
        )
        
        await update.message.reply_text(
            f"✅ Success! All members must now join {f'@{channel}' if not channel.startswith('-') else 'the channel'} to participate here."
        )
    except Exception as e:
        logger.error(f"Error setting channel: {e}")
        await update.message.reply_text(
            "Failed to set channel. Make sure:\n"
            "1. The channel exists\n"
            "2. I'm a member of the channel\n"
            "3. You provided a valid channel identifier"
        )

async def check_membership(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    
    if chat.type == 'private' or user.is_bot:
        return
    
    fsub_data = fsub_collection.find_one({'chat_id': chat.id})
    if not fsub_data:
        return
    
    channel = fsub_data.get('channel')
    channel_id = fsub_data.get('channel_id')
    
    if not channel and not channel_id:
        return
    
    try:
        member = await chat.get_member(user.id)
        if member.status in ['administrator', 'creator']:
            return
        
        # Check membership using either username or ID
        if channel:
            chat_member = await context.bot.get_chat_member(f"@{channel}" if not channel.startswith('-') else channel, user.id)
        else:
            chat_member = await context.bot.get_chat_member(channel_id, user.id)
            
        if chat_member.status in ['left', 'kicked']:
            permissions = ChatPermissions(
                can_send_messages=False,
                can_send_media_messages=False,
                can_send_other_messages=False,
                can_add_web_page_previews=False
            )
            await chat.restrict_member(user.id, permissions)
            
            channel_display = f"@{channel}" if channel and not channel.startswith('-') else "the required channel"
            await update.message.reply_text(
                f"⚠️ {user.mention_html()} has been muted because they haven't joined {channel_display}.\n"
                "Join the channel and contact an admin to be unmuted.",
                parse_mode='HTML'
            )
    except Exception as e:
        logger.error(f"Error checking membership: {e}")
        await update.message.reply_text(
            "⚠️ Error checking channel membership. Make sure I'm admin in the channel."
        )

def main():
    Thread(target=run_flask, daemon=True).start()
    
    application = ApplicationBuilder().token(os.getenv('BOT_TOKEN')).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("fsub", set_fsub_channel))
    application.add_handler(
        MessageHandler(filters.ChatType.GROUPS & ~filters.StatusUpdate.ALL, check_membership)
    )
    
    application.run_polling()

if __name__ == '__main__':
    main()
