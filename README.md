# Telegram Forced Subscription (FSub) Bot

A Telegram bot that enforces channel subscription before allowing users to participate in groups. Mutes users who haven't joined required channels and provides self-unmute functionality.

## Features

- ✅ Force users to join specified channels
- 🔇 Automatically mute non-compliant users (5-minute duration)
- 🔗 Provides join links for both public and private channels
- ✅ Inline "Unmute Me" button with membership verification
- 🗑️ Automatically cleans up old warning messages
- 💾 MongoDB storage for group-channel mappings
- 🚦 Rate-limited error messages to prevent spam
- 🏗️ Ready for deployment with Docker and Render

## Requirements

- Python 3.8+
- MongoDB database
- Telegram Bot Token from [@BotFather](https://t.me/BotFather)
- Admin privileges in both groups and channels

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/telegram-fsub-bot.git
   cd telegram-fsub-bot
