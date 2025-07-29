import os
import logging
import time
from threading import Thread
from datetime import datetime
from flask import Flask
from pymongo import MongoClient
from telegram import Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler
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

# Global variables for bot stats
BOT_START_TIME = time.time()

async def delete_previous_warnings(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Delete all previous warning messages for a user"""
    if 'user_warnings' not in context.chat_data:
        return
    
    # Get all message IDs for this user
    msg_ids = context.chat_data['user_warnings'].get(user_id, [])
    if not isinstance(msg_ids, list):
        msg_ids = [msg_ids]
    
    # Delete each message
    for msg_id in msg_ids:
        try:
            await context.bot.delete_message(
                chat_id=chat_id,
                message_id=msg_id
            )
        except Exception as e:
            logger.warning(f"Could not delete message {msg_id}: {e}")
    
    # Clear stored message IDs
    if user_id in context.chat_data['user_warnings']:
        del context.chat_data['user_warnings'][user_id]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Create inline keyboard with add buttons and support channel
    keyboard = [
        [
            InlineKeyboardButton(
                "➕ Add to Group", 
                url=f"https://t.me/{context.bot.username}?startgroup=true"
            ),
            InlineKeyboardButton(
                "➕ Add to Channel", 
                url=f"https://t.me/{context.bot.username}?startchannel=true"
            )
        ]
    ]
    
    # Add support channel button if configured
    if os.getenv('SUPPORT_CHANNEL'):
        keyboard.append([
            InlineKeyboardButton(
                "📢 Support Channel", 
                url=f"https://t.me/{os.getenv('SUPPORT_CHANNEL')}"
            )
        ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)

    welcome_text = (
        "👋 *Welcome to Force Subscription Bot!*\n\n"
        "I help group admins enforce channel subscriptions by muting users who haven't joined required channels.\n\n"
        "✨ *Features:*\n"
        "• Auto-mute non-subscribed users\n"
        "• 5-minute mute duration\n"
        "• Self-unmute after joining\n"
        "• Supports both public & private channels\n\n"
        "📌 *How to setup:*\n"
        "1. Add me to your group as admin\n"
        "2. Use `/fsub @channel` to set requirements\n"
        "3. I'll handle the rest!\n\n"
        "Click the buttons below to add me to your groups/channels:"
    )

    if update.effective_chat.type == 'private':
        await update.message.reply_text(
            welcome_text,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(
            "I'm a forced subscription bot. Use /fsub to set a required channel for this group.\n\n"
            "ℹ️ I need to be admin in both this group and the channel to work properly.",
            reply_markup=reply_markup
        )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Create keyboard with support channel if configured
    keyboard = []
    if os.getenv('SUPPORT_CHANNEL'):
        keyboard.append([
            InlineKeyboardButton(
                "📢 Support Channel", 
                url=f"https://t.me/{os.getenv('SUPPORT_CHANNEL')}"
            )
        ])
    
    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    
    await update.message.reply_text(
        "⚠️ Admin Requirements:\n"
        "- Make me admin in both group and channel\n"
        "- Grant me 'Restrict users' permission in group\n\n"
        "Commands:\n"
        "/start - Introduction\n"
        "/help - This message\n"
        "/fsub [@channel|ID|reply] - Set required channel\n\n"
        "I'll mute anyone who hasn't joined the required channel for 5 minutes.",
        reply_markup=reply_markup
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
    # Skip if message is forwarded from a channel
    if update.message and update.message.forward_from_chat and update.message.forward_from_chat.type == 'channel':
        return
    
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
            # Mute the user for 5 minutes (300 seconds)
            permissions = ChatPermissions(
                can_send_messages=False,
                can_send_audios=False,
                can_send_documents=False,
                can_send_photos=False,
                can_send_videos=False,
                can_send_video_notes=False,
                can_send_voice_notes=False,
                can_send_polls=False,
                can_send_other_messages=False,
                can_add_web_page_previews=False
            )
            
            try:
                # Calculate mute duration (5 minutes)
                mute_duration = 5 * 60  # 5 minutes in seconds
                until_date = int(time.time()) + mute_duration
                
                await chat.restrict_member(
                    user.id, 
                    permissions,
                    until_date=until_date
                )
                
                # Delete all previous warnings for this user
                await delete_previous_warnings(chat.id, user.id, context)
                
                # Create inline keyboard with unmute button and channel link
                keyboard = []
                
                # Add Unmute button
                keyboard.append([
                    InlineKeyboardButton(
                        "✅ Unmute Me", 
                        callback_data=f"unmute:{chat.id}:{user.id}"
                    )
                ])
                
                # Try to get or create invite link for private channels
                invite_link = None
                try:
                    if channel_id and (not channel or channel.startswith('-')):
                        # For private channels (ID only or ID with negative number)
                        chat_obj = await context.bot.get_chat(channel_id)
                        if chat_obj.invite_link:
                            invite_link = chat_obj.invite_link
                        else:
                            # Create new invite link if none exists
                            invite_link_obj = await context.bot.create_chat_invite_link(
                                chat_id=channel_id,
                                creates_join_request=False,
                                name="FSub Link"
                            )
                            invite_link = invite_link_obj.invite_link
                except Exception as e:
                    logger.warning(f"Could not get/create invite link for channel: {e}")
                
                # Add Channel Join button if link is available
                if channel and not channel.startswith('-'):  # Public channel
                    keyboard.append([
                        InlineKeyboardButton(
                            "🔗 Join Channel", 
                            url=f"https://t.me/{channel}"
                        )
                    ])
                elif invite_link:  # Private channel with invite link
                    keyboard.append([
                        InlineKeyboardButton(
                            "🔗 Join Private Channel", 
                            url=invite_link
                        )
                    ])
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                # Prepare channel display name
                channel_display = ""
                if channel and not channel.startswith('-'):
                    channel_display = f"@{channel}"
                elif channel_id:
                    channel_display = "the private channel"
                else:
                    channel_display = "the required channel"
                
                # Send message with buttons
                warning_msg = await update.message.reply_text(
                    f"⚠️ {user.mention_html()} has been muted for 5 minutes.\n"
                    f"Reason: Not joined {channel_display}\n\n"
                    "After joining, click 'Unmute Me' to verify membership.",
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
                
                # Store the new warning message ID
                if 'user_warnings' not in context.chat_data:
                    context.chat_data['user_warnings'] = {}
                
                # Initialize as list if not already
                if user.id not in context.chat_data['user_warnings']:
                    context.chat_data['user_warnings'][user.id] = []
                elif not isinstance(context.chat_data['user_warnings'][user.id], list):
                    context.chat_data['user_warnings'][user.id] = [context.chat_data['user_warnings'][user.id]]
                
                # Add new message ID
                context.chat_data['user_warnings'][user.id].append(warning_msg.message_id)
                
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

async def unmute_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # Parse callback data: format "unmute:{chat_id}:{user_id}"
    data = query.data.split(':')
    if len(data) != 3 or data[0] != 'unmute':
        await query.edit_message_text("⚠️ Invalid request. Please try again later.")
        return
    
    chat_id = int(data[1])
    user_id = int(data[2])
    
    # Verify the user clicking is the muted user
    if query.from_user.id != user_id:
        await query.answer("❌ This button is only for the muted user!", show_alert=True)
        return
    
    try:
        # Get channel information from MongoDB
        fsub_data = fsub_collection.find_one({'chat_id': chat_id})
        if not fsub_data:
            await query.answer("❌ Configuration error. Please contact admin.", show_alert=True)
            return
        
        channel = fsub_data.get('channel')
        channel_id = fsub_data.get('channel_id')
        
        # Determine channel identifier to use
        target_chat = channel_id if channel_id else (f"@{channel}" if channel and not channel.startswith('-') else channel)
        
        if not target_chat:
            await query.answer("❌ Configuration error. Please contact admin.", show_alert=True)
            return
        
        # Verify user has joined the channel
        try:
            chat_member = await context.bot.get_chat_member(target_chat, user_id)
            if chat_member.status in ['left', 'kicked']:
                await query.answer(
                    "❌ You haven't joined the channel yet! Please join first.",
                    show_alert=True
                )
                return
        except Exception as e:
            logger.error(f"Error verifying membership: {e}")
            await query.answer(
                "⚠️ Error verifying membership. Please try again later.",
                show_alert=True
            )
            return
        
        # Get the chat
        chat = await context.bot.get_chat(chat_id)
        
        # Restore full permissions
        permissions = ChatPermissions(
            can_send_messages=True,
            can_send_audios=True,
            can_send_documents=True,
            can_send_photos=True,
            can_send_videos=True,
            can_send_video_notes=True,
            can_send_voice_notes=True,
            can_send_polls=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True
        )
        
        # Unmute the user
        await chat.restrict_member(user_id, permissions)
        
        # Delete all warning messages for this user
        await delete_previous_warnings(chat_id, user_id, context)
        
        # Update the callback message
        await query.edit_message_text(
            f"✅ {query.from_user.mention_html()} has been unmuted!",
            parse_mode='HTML'
        )
        
        # Notify in the group
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"✅ {query.from_user.mention_html()} has been unmuted after verifying channel membership.",
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Error unmuting user: {e}")
        await query.answer(
            "⚠️ Failed to unmute. Please contact an admin.",
            show_alert=True
        )

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Check if user is owner
    if str(update.effective_user.id) != os.getenv('OWNER_ID'):
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return

    # Calculate uptime
    uptime_seconds = time.time() - BOT_START_TIME
    uptime = str(datetime.timedelta(seconds=int(uptime_seconds)))
    
    # Get groups count
    groups_count = fsub_collection.count_documents({})
    
    # Get bot info
    bot_info = await context.bot.get_me()
    
    # Get MongoDB status
    mongo_status = "Connected" if mongo_client.server_info() else "Disconnected"
    
    # Prepare status message
    status_text = (
        f"🤖 *Bot Status Report*\n\n"
        f"• Bot Name: [{bot_info.full_name}](t.me/{bot_info.username})\n"
        f"• Uptime: `{uptime}`\n"
        f"• Groups Using: `{groups_count}`\n"
        f"• MongoDB: `{mongo_status}`\n\n"
        f"📊 *System Stats*\n"
        f"• Python Version: `{os.sys.version.split()[0]}`\n"
        f"• Platform: `{os.sys.platform}`"
    )
    
    await update.message.reply_text(
        status_text,
        parse_mode='Markdown',
        disable_web_page_preview=True
    )

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Check if user is owner
    if str(update.effective_user.id) != os.getenv('OWNER_ID'):
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return

    # Check if message is replied to
    if not update.message.reply_to_message:
        await update.message.reply_text("ℹ️ Please reply to a message to broadcast it.")
        return

    # Get all group IDs
    group_ids = fsub_collection.distinct("chat_id")
    total_groups = len(group_ids)
    
    if total_groups == 0:
        await update.message.reply_text("ℹ️ No groups to broadcast to.")
        return

    successful = 0
    failed = 0
    failed_ids = []
    
    # Send broadcast with progress
    progress_msg = await update.message.reply_text(
        f"📢 Starting broadcast to {total_groups} groups...\n"
        f"• Sent: 0\n"
        f"• Failed: 0"
    )
    
    for i, chat_id in enumerate(group_ids):
        try:
            await context.bot.copy_message(
                chat_id=chat_id,
                from_chat_id=update.message.reply_to_message.chat_id,
                message_id=update.message.reply_to_message.message_id
            )
            successful += 1
        except Exception as e:
            logger.error(f"Broadcast failed to {chat_id}: {e}")
            failed += 1
            failed_ids.append(chat_id)
        
        # Update progress every 10 messages or at the end
        if (i+1) % 10 == 0 or (i+1) == total_groups:
            try:
                await progress_msg.edit_text(
                    f"📢 Broadcasting to {total_groups} groups...\n"
                    f"• Sent: {successful}\n"
                    f"• Failed: {failed}\n"
                    f"• Progress: {i+1}/{total_groups} ({((i+1)/total_groups)*100:.1f}%)"
                )
            except Exception as e:
                logger.error(f"Error updating progress message: {e}")
    
    # Prepare final report
    report_text = (
        f"✅ Broadcast completed!\n\n"
        f"• Total groups: {total_groups}\n"
        f"• Successful: {successful}\n"
        f"• Failed: {failed}"
    )
    
    # Add failed IDs if any
    if failed > 0:
        report_text += f"\n\n❌ Failed IDs:\n{', '.join(map(str, failed_ids[:20]))}"
        if failed > 20:
            report_text += f"\n... and {failed-20} more"
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=report_text
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
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(
        MessageHandler(filters.ChatType.GROUPS & ~filters.StatusUpdate.ALL, check_membership)
    )
    application.add_handler(CallbackQueryHandler(unmute_button, pattern=r"^unmute:"))
    
    # Start polling
    application.run_polling()

if __name__ == '__main__':
    main()