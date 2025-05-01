# bot.py
import logging
import os
import json
import httpx
import asyncio
from dotenv import load_dotenv
import pytz # Keep pytz import

# Import escape_markdown just in case, though we won't use it by default now
from telegram.helpers import escape_markdown

from telegram import Update, constants, ReplyKeyboardRemove, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    JobQueue,
    PicklePersistence
)
# Added BadRequest for specific handling
from telegram.error import RetryAfter, TimedOut, BadRequest

# --- Configuration ---
load_dotenv() # Load environment variables from .env file

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_API_KEY = os.getenv("CHAT_API_KEY")
CHAT_API_BASE_URL = os.getenv("CHAT_API_BASE_URL")
PERSISTENCE_PATH = "/data/bot_persistence.pkl" # Define persistence file path

if not TELEGRAM_TOKEN or not CHAT_API_KEY:
    raise ValueError("TELEGRAM_BOT_TOKEN and CHAT_API_KEY must be set in .env file")

CHAT_ENDPOINT = f"{CHAT_API_BASE_URL}/chat-messages"
HEADERS = {
    "Authorization": f"Bearer {CHAT_API_KEY}",
    "Content-Type": "application/json",
}

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


# --- Helper Function: Try Markdown, Fallback to Plain Text ---

async def edit_message_safe(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, text: str):
    """
    Safely attempts to edit a message.
    Tries first with MARKDOWNV2 parse mode. If that fails due to parsing entities,
    falls back to sending the message without any parse mode (plain text).
    Handles RetryAfter, TimedOut, and other common errors.
    """
    # Ensure text is a string and not empty for editing
    text_to_send = text or "..."
    last_text_key = f"last_edit_{chat_id}_{message_id}"
    last_sent_content = context.bot_data.get(last_text_key)

    # Avoid editing if the new text is identical to the last successfully sent text
    if last_sent_content == text_to_send:
        # logger.debug(f"Skipping edit for {message_id} as content is identical.")
        return

    try:
        # --- Attempt 1: Try with Markdown ---
        # logger.debug(f"Attempting edit for {message_id} with Markdown.")
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text_to_send,
            # Using MARKDOWN_V2 is generally recommended for better consistency
            # If your API strictly produces original Markdown, you might use constants.ParseMode.MARKDOWN
            # but MARKDOWN_V2 is safer with potential API outputs.
            parse_mode=constants.ParseMode.MARKDOWN # Or MARKDOWN_V2 if preferred
        )
        # If successful, update the cache with the text that was successfully sent (original formatting)
        context.bot_data[last_text_key] = text_to_send
        # logger.debug(f"Successfully edited message {message_id} with Markdown.")

    except BadRequest as e:
        # --- Handle Markdown Parsing Failure ---
        if "Can't parse entities" in str(e):
            logger.warning(
                f"Markdown parsing failed for message {message_id}: {e}. "
                f"Falling back to plain text. Problematic text snippet: '{text_to_send[:100]}...'"
            )
            try:
                # --- Attempt 2: Fallback to Plain Text ---
                # Check cache again to avoid re-sending identical plain text if previous attempt was plain fallback
                if last_sent_content != text_to_send: # Ensure we are actually changing the content
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=text_to_send,
                        parse_mode=None # Send as plain text
                    )
                    # Update cache with the plain text version that was successfully sent
                    context.bot_data[last_text_key] = text_to_send
                    logger.info(f"Successfully edited message {message_id} with plain text after Markdown failure.")
                # else:
                #     logger.debug(f"Skipping plain text fallback edit for {message_id} as content is identical to last sent.")

            except BadRequest as fallback_e:
                 # Handle cases where even plain text fails (e.g., message too long, other restrictions)
                 logger.error(f"Plain text fallback edit for message {message_id} also failed with BadRequest: {fallback_e}", exc_info=True)
            except Exception as fallback_e:
                logger.error(f"Plain text fallback edit for message {message_id} failed unexpectedly: {fallback_e}", exc_info=True)
        else:
            # Handle other BadRequest errors (e.g., message not found, chat not found)
            logger.error(f"BadRequest while editing message {message_id} (not a parsing error): {e}", exc_info=True)

    except RetryAfter as e:
        logger.warning(f"Rate limited while editing message {message_id}: Sleeping for {e.retry_after}s")
        await asyncio.sleep(e.retry_after)
        await edit_message_safe(context, chat_id, message_id, text) # Retry the whole process

    except TimedOut:
        logger.warning(f"Timed out while editing message {message_id}. Skipping.")

    except Exception as e:
        # Catch "Message is not modified" - this is not an error, but means the content was already identical
        # according to Telegram, even if our cache thought it was different. Update cache.
        if "Message is not modified" in str(e):
             logger.debug(f"Message {message_id} not modified (Telegram). Updating cache.")
             context.bot_data[last_text_key] = text_to_send # Update cache to reflect Telegram's state
        else:
            # Log other unexpected errors
            logger.error(f"Failed to edit message {message_id} with unexpected error: {e}", exc_info=True)

# --- Process Stream Response (Unchanged, relies on edit_message_safe) ---

async def process_stream_response(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    stream_response: httpx.Response,
    placeholder_message_id: int
):
    """Processes the SSE stream from the API and updates the Telegram message."""
    full_answer = ""
    current_task_id = None
    current_conversation_id = context.chat_data.get("conversation_id")
    new_conversation_id = None
    buffer = ""
    last_edit_time = 0
    edit_interval = 1.5 # Seconds between edits

    try:
        async for line in stream_response.aiter_lines():
            if line.startswith("data:"):
                data_str = line[len("data:"):].strip()
                if not data_str:
                    continue

                try:
                    data = json.loads(data_str)
                    event_type = data.get("event")

                    if "conversation_id" in data and data["conversation_id"]:
                        new_conversation_id = data["conversation_id"]
                        if new_conversation_id != current_conversation_id:
                           context.chat_data["conversation_id"] = new_conversation_id
                           current_conversation_id = new_conversation_id
                           logger.info(f"Updated persisted conversation_id to: {new_conversation_id} for chat {update.effective_chat.id}")

                    if "task_id" in data and data["task_id"]:
                        current_task_id = data["task_id"]

                    if event_type == "message":
                        answer_chunk = data.get("answer", "")
                        full_answer += answer_chunk
                        buffer += answer_chunk

                        current_time = asyncio.get_event_loop().time()
                        if current_time - last_edit_time > edit_interval and buffer:
                            await edit_message_safe(context, update.effective_chat.id, placeholder_message_id, full_answer + "...")
                            buffer = ""
                            last_edit_time = current_time

                    elif event_type == "agent_thought":
                        pass

                    elif event_type == "message_end":
                        logger.info(f"Stream ended for conversation {new_conversation_id or current_conversation_id}. Finalizing message.")
                        # Final edit with the complete answer
                        await edit_message_safe(context, update.effective_chat.id, placeholder_message_id, full_answer)
                        if new_conversation_id and new_conversation_id != context.chat_data.get("conversation_id"):
                             context.chat_data["conversation_id"] = new_conversation_id
                             logger.info(f"Persisted final conversation_id: {new_conversation_id} for chat {update.effective_chat.id}")
                        break

                    elif event_type == "error":
                        error_msg = data.get('message', 'Unknown streaming error')
                        status_code = data.get('status', 'N/A')
                        logger.error(f"Error in stream: {status_code} - {error_msg}")
                        await edit_message_safe(context, update.effective_chat.id, placeholder_message_id, f"An error occurred during generation: {error_msg}")
                        if new_conversation_id and new_conversation_id != context.chat_data.get("conversation_id"):
                             context.chat_data["conversation_id"] = new_conversation_id
                             logger.info(f"Persisted conversation_id: {new_conversation_id} after stream error for chat {update.effective_chat.id}")
                        return

                    elif event_type == "ping":
                        pass

                except json.JSONDecodeError:
                    logger.error(f"Failed to decode JSON from stream: {data_str}")
                except Exception as e:
                    logger.error(f"Error processing stream data chunk: {e}", exc_info=True)
                    await edit_message_safe(context, update.effective_chat.id, placeholder_message_id, f"Error processing response: {type(e).__name__}")
                    if new_conversation_id and new_conversation_id != context.chat_data.get("conversation_id"):
                        context.chat_data["conversation_id"] = new_conversation_id
                        logger.info(f"Persisted conversation_id: {new_conversation_id} after processing error for chat {update.effective_chat.id}")
                    return

        # Ensure final message is updated if loop finished without message_end event but buffer might exist
        # This final edit call is crucial.
        if buffer: # If there's remaining buffer content (unlikely with message_end but safe check)
             logger.info(f"Stream loop finished with remaining buffer. Performing final edit for message {placeholder_message_id}.")
             await edit_message_safe(context, update.effective_chat.id, placeholder_message_id, full_answer)
        # Check if final ID needs storing (might be redundant due to mid-stream/message_end checks, but safe)
        if new_conversation_id and new_conversation_id != context.chat_data.get("conversation_id"):
            context.chat_data["conversation_id"] = new_conversation_id
            logger.info(f"Stored final runtime conversation_id: {context.chat_data['conversation_id']} for chat {update.effective_chat.id} after stream loop.")
        elif not context.chat_data.get("conversation_id") and not new_conversation_id:
            logger.warning(f"No conversation ID received or stored for chat {update.effective_chat.id} by end of stream.")

    except httpx.StreamError as e:
        logger.error(f"Stream error while reading response: {e}", exc_info=True)
        await edit_message_safe(context, update.effective_chat.id, placeholder_message_id, "Connection lost during response generation.")
        if new_conversation_id and new_conversation_id != context.chat_data.get("conversation_id"):
             context.chat_data["conversation_id"] = new_conversation_id
             logger.info(f"Persisted conversation_id: {new_conversation_id} after stream connection error for chat {update.effective_chat.id}")
    except Exception as e:
        logger.error(f"Unexpected error in process_stream_response: {e}", exc_info=True)
        await edit_message_safe(context, update.effective_chat.id, placeholder_message_id, f"An unexpected error occurred while processing the response: {type(e).__name__}")
        if new_conversation_id and new_conversation_id != context.chat_data.get("conversation_id"):
             context.chat_data["conversation_id"] = new_conversation_id
             logger.info(f"Persisted conversation_id: {new_conversation_id} after unexpected error in stream processing for chat {update.effective_chat.id}")


# --- API Request Function (Unchanged, relies on process_stream_response) ---
async def make_chat_api_request_and_process(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    query: str,
    placeholder_message_id: int
):
    """Makes request, handles stream context manager, and calls processor."""
    user = update.effective_user
    user_id = str(user.id)
    first_name = user.first_name
    username = user.username or ""
    conversation_id = context.chat_data.get("conversation_id")

    payload = {
        "inputs": {}, "query": query, "response_mode": "streaming", "user": user_id,
        "user_details": {"first_name": first_name, "username": username},
        "files": [], "auto_generate_name": True
    }
    if conversation_id: payload["conversation_id"] = conversation_id

    logger.info(
        f"Initiating Chat API stream request for user {user_id} "
        f"(Name: '{first_name}', Username: '@{username}' if username else 'N/A'), "
        f"Conversation: {conversation_id}"
    )

    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
             async with client.stream("POST", CHAT_ENDPOINT, headers=HEADERS, json=payload) as response:
                try:
                    # Check status early if possible
                    if response.status_code >= 400:
                        error_body = await response.aread()
                        response.raise_for_status() # Will likely raise HTTPStatusError

                    await process_stream_response(update, context, response, placeholder_message_id)

                except httpx.HTTPStatusError as e:
                    logger.error(f"HTTP Status Error from Chat API stream: {e.response.status_code} - {e.response.text}")
                    error_detail = "Unknown error."
                    try:
                        error_text = e.response.text
                        try: error_data = json.loads(error_text); error_detail = error_data.get('message', error_text)
                        except json.JSONDecodeError: error_detail = error_text
                    except Exception: error_detail = f"Status code {e.response.status_code}"

                    if e.response.status_code == 401: error_detail = "Authentication failed (Check CHAT_API_KEY)."
                    elif e.response.status_code == 404: error_detail = f"API endpoint not found ({CHAT_ENDPOINT})."
                    elif e.response.status_code == 429: error_detail = "Rate limit exceeded."
                    elif e.response.status_code >= 500: error_detail = "Chat service internal error."

                    await edit_message_safe(context, update.effective_chat.id, placeholder_message_id, f"Sorry, there was an error from the chat service: {e.response.status_code} ({error_detail})")

    except httpx.RequestError as e:
        logger.error(f"HTTP Request Error connecting to Chat API ({CHAT_ENDPOINT}): {e}")
        await edit_message_safe(context, update.effective_chat.id, placeholder_message_id, f"Sorry, I couldn't connect to the chat service ({type(e).__name__}). Please try again later.")
    except Exception as e:
        logger.error(f"Unexpected error during Chat API request/stream setup: {e}", exc_info=True)
        await edit_message_safe(context, update.effective_chat.id, placeholder_message_id, f"An unexpected error occurred: {type(e).__name__}")

    # Clean up the edit cache for this message ID after processing is done or fails
    last_text_key = f"last_edit_{update.effective_chat.id}_{placeholder_message_id}"
    context.bot_data.pop(last_text_key, None)
    logger.debug(f"Cleaned up edit cache key for message {placeholder_message_id}")


# --- Telegram Command Handlers (Unchanged) ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    context.chat_data.pop("conversation_id", None)
    logger.info(f"User {user.id} ({user.first_name} @{user.username}) started bot. Cleared persistent conversation state for chat {update.effective_chat.id}.")
    await update.message.reply_html(rf"Hi {user.mention_html()}! 👋", reply_markup=ReplyKeyboardRemove())
    await update.message.reply_text("I'm ready to chat! Send me a message.\nUse /new to start a fresh conversation.")

async def new_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    context.chat_data.pop("conversation_id", None)
    logger.info(f"User {user.id} ({user.first_name} @{user.username}) requested new conversation in chat {update.effective_chat.id}. Persistent state cleared.")
    await update.message.reply_text("Okay, let's start a new conversation! Your previous chat history for this session will be ignored.")


# --- Message Handler (Unchanged, relies on make_chat_api_request_and_process) ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        logger.debug("Received message update without text, ignoring.")
        return

    user = update.effective_user; chat_id = update.effective_chat.id; query = update.message.text
    logger.info(
        f"Received message from {user.id} ({user.first_name} @{user.username}) in chat {chat_id}. "
        f"Current Persistent Conversation ID: {context.chat_data.get('conversation_id')}"
    )

    placeholder_msg = await update.message.reply_text("🧠 Thinking...", reply_to_message_id=update.message.message_id)
    logger.debug(f"Sent placeholder message {placeholder_msg.message_id} in chat {chat_id}")
    context.bot_data.pop(f"last_edit_{chat_id}_{placeholder_msg.message_id}", None) # Clear cache for new message

    await make_chat_api_request_and_process(update, context, query, placeholder_msg.message_id)


# --- Error Handler (Unchanged) ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if isinstance(context.error, telegram.error.NetworkError) and "Bad Gateway" in str(context.error):
        logger.warning(f"Telegram polling failed with NetworkError: {context.error}. Retrying is handled by the library.")
        return

    logger.error(f"Update {update} caused error {context.error}", exc_info=context.error)
    # if isinstance(update, Update) and update.effective_message:
    #     try: await update.effective_message.reply_text("Sorry, something went wrong.")
    #     except Exception as e: logger.error(f"Failed to notify user about error: {e}")


# --- Main Bot Execution (Unchanged) ---
def main() -> None:
    persistence_dir = os.path.dirname(PERSISTENCE_PATH)
    try:
        os.makedirs(persistence_dir, exist_ok=True)
        logger.info(f"Persistence directory '{persistence_dir}' checked/created.")
    except OSError as e:
        logger.error(f"Could not create persistence directory '{persistence_dir}': {e}", exc_info=True); raise

    persistence = PicklePersistence(filepath=PERSISTENCE_PATH)
    logger.info(f"Using PicklePersistence at: {PERSISTENCE_PATH}")

    application = Application.builder().token(TELEGRAM_TOKEN).persistence(persistence).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("new", new_conversation))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)

    logger.info("Starting bot polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()