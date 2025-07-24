import os
import logging
import time
from threading import Thread
from flask import Flask
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
            "Hi! I'm a forced subscription bot. Add me to a group and use /fsub to set a channel.\n\n"
            "⚠️ Requirements:\n"
            "- Make me admin in both group and channel\n"
            "- Grant me 'Restrict users' permission"
        )
    else:
        await update.message.reply_text(
            "I'm a forced subscription bot. Use /fsub to set a required channel for this group.\n\n"
            "ℹ️ I need to be admin in both this group and the channel to work properly."
        )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚠️ Admin Requirements:\n"
        "- Make me admin in both group and channel\n"
        "- Grant me 'Restrict users' permission in group\n\n"
        "Commands:\n"
        "/start - Introduction\n"
        "/help - This message\n"
        "/fsub [@channel|ID|reply] - Set required channel\n\n"
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
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    
    # Check if replying to a channel message
    if update.message.reply_to_message and update.message.reply_to_message.sender_chat:
        if update.message.reply_to_message.sender_chat.type == 'channel':
            channel = update.message.reply_to_message.sender_chat.username or str(update.message.reply_to_message.sender_chat.id)
            await save_fsub_channel(chat.id, channel, update, context)
            return
    
    # Check for channel ID or @username in arguments
    if context.args:
        channel_input = context.args[0]
        if channel_input.startswith('@'):
            channel = channel_input[1:]
        elif channel_input.isdigit() or (channel_input.startswith('-') and channel_input[1:].isdigit()):
            channel = channel_input
        else:
            await update.message.reply_text("❌ Invalid channel format. Use @username, channel ID, or reply to a channel message.")
            return
        
        await save_fsub_channel(chat.id, channel, update, context)
    else:
        await update.message.reply_text(
            "Usage:\n"
            "/fsub @channelusername\n"
            "/fsub channel_id\n"
            "Or reply to a channel message with /fsub"
        )

async def save_fsub_channel(chat_id: int, channel: str, update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Verify the channel exists and get its ID
        chat = await context.bot.get_chat(f"@{channel}" if not channel.startswith('-') else channel)
        if chat.type != 'channel':
            await update.message.reply_text("❌ The specified chat is not a channel.")
            return
        
        # Save to MongoDB
        fsub_collection.update_one(
            {'chat_id': chat_id},
            {'$set': {'channel': channel, 'channel_id': chat.id}},
            upsert=True
        )
        
        # Verify bot permissions in channel
        try:
            bot_member = await context.bot.get_chat_member(chat.id, context.bot.id)
            if bot_member.status not in ['administrator', 'creator']:
                await update.message.reply_text(
                    "⚠️ Warning: I'm not admin in that channel.\n"
                    "I won't be able to check memberships until you make me admin."
                )
                return
            
            await update.message.reply_text(
                f"✅ Success! All members must now join {f'@{channel}' if not channel.startswith('-') else 'the channel'} to participate here."
            )
        except Exception as perm_error:
            logger.error(f"Permission check error: {perm_error}")
            await update.message.reply_text(
                "⚠️ Warning: I can't check my permissions in that channel.\n"
                "Make sure I'm added as admin to the channel."
            )
            
    except Exception as e:
        logger.error(f"Error setting channel: {e}")
        await update.message.reply_text(
            "❌ Failed to set channel. Make sure:\n"
            "1. The channel exists\n"
            "2. I'm a member of the channel\n"
            "3. You provided a valid channel identifier"
        )

async def check_membership(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    
    # Skip checks in private chats or from bots
    if chat.type == 'private' or user.is_bot:
        return
    
    # Get fsub data from MongoDB
    fsub_data = fsub_collection.find_one({'chat_id': chat.id})
    if not fsub_data:
        return
    
    channel = fsub_data.get('channel')
    channel_id = fsub_data.get('channel_id')
    
    try:
        # Check if user is admin (exempt from checks)
        member = await chat.get_member(user.id)
        if member.status in ['administrator', 'creator']:
            return
        
        # Determine channel identifier to use
        target_chat = channel_id if channel_id else (f"@{channel}" if channel and not channel.startswith('-') else channel)
        
        if not target_chat:
            logger.warning(f"No valid channel identifier found for chat {chat.id}")
            return
        
        # Verify bot's permissions in target channel
        try:
            bot_member = await context.bot.get_chat_member(target_chat, context.bot.id)
            if bot_member.status not in ['administrator', 'creator']:
                # Only show this error once per hour per group to avoid spamming
                last_warning = context.chat_data.get('last_channel_warning', 0)
                current_time = time.time()
                if current_time - last_warning > 3600:
                    await update.message.reply_text(
                        "⚠️ I need admin in the channel to check memberships.\n"
                        "Please make me admin or update /fsub settings."
                    )
                    context.chat_data['last_channel_warning'] = current_time
                return
        except Exception as perm_error:
            logger.error(f"Permission check error: {perm_error}")
            return
        
        # Check user's membership in channel
        chat_member = await context.bot.get_chat_member(target_chat, user.id)
        if chat_member.status in ['left', 'kicked']:
            # Mute the user with updated permissions syntax
            permissions = ChatPermissions(
                can_send_messages=False,       # No text messages
                can_send_audios=False,         # No audio files
                can_send_documents=False,      # No documents
                can_send_photos=False,         # No photos
                can_send_videos=False,         # No videos
                can_send_video_notes=False,    # No video notes
                can_send_voice_notes=False,    # No voice notes
                can_send_polls=False,          # No polls
                can_send_other_messages=False, # No stickers, GIFs, etc
                can_add_web_page_previews=False # No link previews
            )
            
            try:
                await chat.restrict_member(user.id, permissions)
                channel_display = f"@{channel}" if channel and not channel.startswith('-') else "the required channel"
                await update.message.reply_text(
                    f"⚠️ {user.mention_html()} has been muted for not joining {channel_display}.\n"
                    "Join the channel and contact an admin to be unmuted.",
                    parse_mode='HTML'
                )
            except Exception as mute_error:
                logger.error(f"Error muting user: {mute_error}")
                last_mute_error = context.chat_data.get('last_mute_error', 0)
                current_time = time.time()
                if current_time - last_mute_error > 3600:
                    await update.message.reply_text(
                        "⚠️ Failed to mute user. Make sure I have 'Restrict users' permission in this group."
                    )
                    context.chat_data['last_mute_error'] = current_time
    
    except Exception as e:
        logger.error(f"Error in membership check: {e}")

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
