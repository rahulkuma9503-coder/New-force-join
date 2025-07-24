# Force Join Telegram Bot

A Telegram bot that enforces channel/group joining before allowing users to chat in a group.

## Features
- Checks if users are members of required channels/groups
- Mutes non-compliant users for 5 minutes
- Deletes their previous messages
- Admin configuration interface

## Deployment on Render

1. **Create a new Web Service** on Render
2. Connect your GitHub/GitLab repository or use manual deployment
3. Set the following environment variables:
   - `TELEGRAM_BOT_TOKEN`: Your bot token from @BotFather
   - `ADMIN_IDS`: Comma-separated list of admin user IDs
   - `REQUIRED_CHATS`: Comma-separated list of chat IDs that users must join
   - `BOT_USERNAME`: Your bot's username (without @)
4. Set the build command to: `pip install -r requirements.txt`
5. Deploy!

## Bot Commands
- `/start` - Basic introduction
- `/help` - Show help message
- `/settings` - Admin configuration (in groups)

## Requirements
- The bot must be an admin in all required chats
- The bot needs the following permissions in the groups where it operates:
  - Delete messages
  - Restrict members
  - Send messages