# set_commands.py
import asyncio
import os
import logging
from dotenv import load_dotenv
from telegram import Bot, BotCommand

# --- Configuration ---
load_dotenv() # Load environment variables from .env file

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN must be set in .env file")

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Define Bot Commands ---
# (Adjust these to match your bot's actual commands and descriptions)
BOT_COMMANDS = [
    BotCommand("start", "👋 Start interacting with the bot"),
    BotCommand("new", "🔄 Start a new conversation"),
    # Add other commands here if you have them
    # BotCommand("help", "ℹ️ Show help message"),
]

async def set_bot_commands():
    """Sets the bot's command list on Telegram."""
    bot = Bot(token=TELEGRAM_TOKEN)
    try:
        logger.info(f"Attempting to set commands: {BOT_COMMANDS}")
        await bot.set_my_commands(commands=BOT_COMMANDS)
        logger.info("Successfully set bot commands.")
    except Exception as e:
        logger.error(f"Failed to set bot commands: {e}", exc_info=True)
    finally:
        # Close the bot instance to release resources if needed by the library version
        # In newer versions, this might happen automatically or context managers are preferred
        # await bot.shutdown() # Or similar method if available/needed
        pass

if __name__ == "__main__":
    logger.info("Running command setting script...")
    asyncio.run(set_bot_commands())
    logger.info("Command setting script finished.")