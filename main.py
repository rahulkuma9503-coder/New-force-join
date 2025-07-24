import os
import logging
from typing import Dict, List
from datetime import datetime, timedelta

from telegram import (
    Update,
    ChatPermissions,
    Chat,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackContext,
    CallbackQueryHandler,
)

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Bot configuration
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
ADMIN_IDS = list(map(int, os.getenv('ADMIN_IDS', '').split(',')))
REQUIRED_CHATS = list(map(int, os.getenv('REQUIRED_CHATS', '').split(',')))
BOT_USERNAME = os.getenv('BOT_USERNAME')  # Without @

# Mute duration in minutes
MUTE_DURATION = 5

# Store user's last message ID to delete it
user_last_message: Dict[int, int] = {}

class ForceJoinBot:
    def __init__(self):
        self.updater = Updater(TOKEN, use_context=True)
        self.dp = self.updater.dispatcher

        # Add handlers
        self.dp.add_handler(CommandHandler("start", self.start))
        self.dp.add_handler(CommandHandler("help", self.help))
        self.dp.add_handler(CommandHandler("settings", self.settings, filters=Filters.chat_type.groups))
        self.dp.add_handler(MessageHandler(Filters.text & ~Filters.command & Filters.chat_type.groups, self.check_membership))
        self.dp.add_handler(CallbackQueryHandler(self.button))

        # Error handler
        self.dp.add_error_handler(self.error)

    def start(self, update: Update, context: CallbackContext):
        """Send a message when the command /start is issued."""
        user = update.effective_user
        update.message.reply_text(
            f"Hi {user.first_name}! I'm a bot that enforces channel joining rules in groups.\n\n"
            "Admins can use /settings in groups to configure me."
        )

    def help(self, update: Update, context: CallbackContext):
        """Send a message when the command /help is issued."""
        update.message.reply_text(
            "I help enforce channel joining rules in groups.\n\n"
            "Admins can use these commands:\n"
            "/settings - Configure the bot in a group\n"
            "/help - Show this help message"
        )

    def settings(self, update: Update, context: CallbackContext):
        """Show settings for admins."""
        if update.effective_user.id not in ADMIN_IDS:
            update.message.reply_text("You need to be an admin to use this command.")
            return

        keyboard = [
            [InlineKeyboardButton("Set Required Chats", callback_data='set_chats')],
            [InlineKeyboardButton("Check Configuration", callback_data='check_config')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        update.message.reply_text('Admin Settings:', reply_markup=reply_markup)

    def button(self, update: Update, context: CallbackContext):
        """Handle button presses."""
        query = update.callback_query
        query.answer()

        if query.data == 'set_chats':
            query.edit_message_text(
                "To set required chats, please set the REQUIRED_CHATS environment variable "
                "with comma-separated chat IDs where the bot is added as admin."
            )
        elif query.data == 'check_config':
            config_text = (
                f"Current Configuration:\n"
                f"Admin IDs: {ADMIN_IDS}\n"
                f"Required Chats: {REQUIRED_CHATS}\n"
                f"Mute Duration: {MUTE_DURATION} minutes"
            )
            query.edit_message_text(config_text)

    async def check_membership(self, update: Update, context: CallbackContext):
        """Check if user is member of required chats."""
        user = update.effective_user
        chat = update.effective_chat

        # Delete the user's previous message if exists
        if user.id in user_last_message:
            try:
                await context.bot.delete_message(chat.id, user_last_message[user.id])
            except Exception as e:
                logger.warning(f"Could not delete message: {e}")

        # Store the current message ID
        user_last_message[user.id] = update.message.message_id

        # Check if user is member of all required chats
        not_joined_chats = []
        for chat_id in REQUIRED_CHATS:
            try:
                member = await context.bot.get_chat_member(chat_id, user.id)
                if member.status in ['left', 'kicked']:
                    not_joined_chats.append(chat_id)
            except Exception as e:
                logger.error(f"Error checking membership: {e}")
                continue

        if not_joined_chats:
            # User hasn't joined some required chats
            mute_until = datetime.now() + timedelta(minutes=MUTE_DURATION)
            permissions = ChatPermissions(
                can_send_messages=False,
                can_send_media_messages=False,
                can_send_polls=False,
                can_send_other_messages=False,
                can_add_web_page_previews=False,
                can_change_info=False,
                can_invite_users=False,
                can_pin_messages=False,
            )

            try:
                await context.bot.restrict_chat_member(
                    chat.id,
                    user.id,
                    permissions,
                    until_date=mute_until
                )

                # Create invite links for the required chats
                buttons = []
                for chat_id in not_joined_chats:
                    try:
                        chat_info = await context.bot.get_chat(chat_id)
                        invite_link = await chat_info.export_invite_link()
                        buttons.append([InlineKeyboardButton(
                            f"Join {chat_info.title}",
                            url=invite_link
                        )])
                    except Exception as e:
                        logger.error(f"Error getting chat info: {e}")
                        continue

                buttons.append([InlineKeyboardButton(
                    "I've joined all channels",
                    callback_data=f"check_again:{user.id}"
                )])

                reply_markup = InlineKeyboardMarkup(buttons)
                warning_msg = await update.message.reply_text(
                    f"⚠️ {user.mention_html()} you must join all required channels to participate here.\n\n"
                    "You've been muted for 5 minutes. Join the channels below and click the button to verify.",
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )

                # Delete the warning message after mute duration
                context.job_queue.run_once(
                    self.delete_warning,
                    MUTE_DURATION * 60,
                    context=(chat.id, warning_msg.message_id, user.id)
                )

            except Exception as e:
                logger.error(f"Error restricting user: {e}")
                await update.message.reply_text(
                    "⚠️ An error occurred while processing your message. Please try again."
                )

    async def delete_warning(self, context: CallbackContext):
        """Delete the warning message after mute duration."""
        chat_id, message_id, user_id = context.job.context
        try:
            await context.bot.delete_message(chat_id, message_id)
        except Exception as e:
            logger.warning(f"Could not delete warning message: {e}")

        # Restore user permissions
        permissions = ChatPermissions(
            can_send_messages=True,
            can_send_media_messages=True,
            can_send_polls=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True,
            can_change_info=False,
            can_invite_users=False,
            can_pin_messages=False,
        )
        try:
            await context.bot.restrict_chat_member(chat_id, user_id, permissions)
        except Exception as e:
            logger.error(f"Error restoring user permissions: {e}")

    def error(self, update: Update, context: CallbackContext):
        """Log Errors caused by Updates."""
        logger.warning('Update "%s" caused error "%s"', update, context.error)

    def run(self):
        """Start the bot."""
        self.updater.start_polling()
        self.updater.idle()

if __name__ == '__main__':
    bot = ForceJoinBot()
    bot.run()
