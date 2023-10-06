import logging
import logging.config
import os
import sys
from telegram.ext import CommandHandler, Filters, MessageHandler, Updater


sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.handlers import *

load_dotenv("app/.env")

def error(update, context):
    """Log Errors caused by Updates."""

    logging.warning('Update "%s" ', update)
    logging.exception(context.error)


def main():
    updater = Updater(DefaultConfig.TELEGRAM_TOKEN, use_context=True)

    dp = updater.dispatcher

    # command handlers
    set_commands_handler(dp)

    # message handler
    dp.add_handler(MessageHandler(Filters.text, echo))

    # log all errors
    dp.add_error_handler(error)

    # Start the Bot
    if DefaultConfig.MODE == "webhook":
        updater.start_webhook(
            listen="0.0.0.0",
            port=int(DefaultConfig.PORT),
            url_path=DefaultConfig.TELEGRAM_TOKEN,
            webhook_url=DefaultConfig.WEBHOOK_URL + DefaultConfig.TELEGRAM_TOKEN
        )

        logging.info(f"Start webhook mode on port {DefaultConfig.PORT}")
    else:
        updater.start_polling()
        logging.info(f"Start polling mode")

    updater.idle()

class DefaultConfig:
    PORT = int(os.environ.get("PORT", 5000))
    TELEGRAM_TOKEN = os.environ.get("API_TELEGRAM", "")
    MODE = os.environ.get("MODE", "webhook")
    WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

    @staticmethod
    def init_logging():
        logging.basicConfig(
            format="%(asctime)s - %(levelname)s - %(message)s",
            level=DefaultConfig.LOG_LEVEL,
        )

if __name__ == "__main__":
    # Enable logging
    DefaultConfig.init_logging()
    logging.info(f"PORT: {DefaultConfig.PORT}")
    main()
