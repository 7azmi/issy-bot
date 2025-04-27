# bot.py
import logging
import os
import json
import httpx
import asyncio
from dotenv import load_dotenv
import pytz # Keep pytz import

from telegram import Update, constants, ReplyKeyboardRemove, BotCommand # Added BotCommand for consistency if needed later
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    JobQueue,
    PicklePersistence # Added for persistence
)
from telegram.error import RetryAfter, TimedOut

# --- Configuration ---
load_dotenv() # Load environment variables from .env file

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_API_KEY = os.getenv("CHAT_API_KEY")
CHAT_API_BASE_URL = os.getenv("CHAT_API_BASE_URL")
PERSISTENCE_PATH = "/data/bot_persistence.pkl" # Define persistence file path (inside container)

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


# --- Helper Functions ---

async def edit_message_safe(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, text: str):
    """Safely attempts to edit a message, handling potential errors."""
    try:
        # Use bot_data for message edit cache to avoid potential conflicts with persisted chat_data
        last_text_key = f"last_edit_{chat_id}_{message_id}"
        if context.bot_data.get(last_text_key) != text:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text or "...", # Ensure non-empty text
                parse_mode=constants.ParseMode.MARKDOWN
            )
            context.bot_data[last_text_key] = text # Store the last edited text
    except RetryAfter as e:
        logger.warning(f"Rate limited while editing message: Sleeping for {e.retry_after}s")
        await asyncio.sleep(e.retry_after)
        await edit_message_safe(context, chat_id, message_id, text) # Retry after waiting
    except TimedOut:
        logger.warning("Timed out while editing message. Skipping.")
    except Exception as e:
        if "Message is not modified" in str(e):
             logger.debug(f"Message {message_id} not modified, skipping edit.")
             # Still update our record even if Telegram didn't change it
             context.bot_data[last_text_key] = text
        else:
            logger.error(f"Failed to edit message {message_id}: {e}", exc_info=True)

async def process_stream_response(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    stream_response: httpx.Response,
    placeholder_message_id: int
):
    """Processes the SSE stream from the API and updates the Telegram message."""
    full_answer = ""
    current_task_id = None
    # Get conversation_id from persisted chat_data
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
                        # Update persisted chat_data immediately if a new ID arrives
                        if new_conversation_id != current_conversation_id:
                           context.chat_data["conversation_id"] = new_conversation_id
                           current_conversation_id = new_conversation_id # Update local var too
                           logger.info(f"Updated persisted conversation_id to: {new_conversation_id} for chat {update.effective_chat.id}")


                    if "task_id" in data and data["task_id"]:
                        current_task_id = data["task_id"]

                    if event_type == "message":
                        answer_chunk = data.get("answer", "")
                        full_answer += answer_chunk
                        buffer += answer_chunk

                        current_time = asyncio.get_event_loop().time()
                        if current_time - last_edit_time > edit_interval:
                            if buffer:
                                await edit_message_safe(context, update.effective_chat.id, placeholder_message_id, full_answer + "...")
                                buffer = ""
                                last_edit_time = current_time

                    # ... (rest of the event handling remains the same) ...
                    elif event_type == "agent_thought":
                        pass # logger.debug(f"Agent thought: {data}")

                    elif event_type == "message_end":
                        logger.info(f"Stream ended for conversation {new_conversation_id or current_conversation_id}")
                        await edit_message_safe(context, update.effective_chat.id, placeholder_message_id, full_answer)
                        # Ensure final conversation ID is persisted if received at the end
                        if new_conversation_id and new_conversation_id != context.chat_data.get("conversation_id"):
                             context.chat_data["conversation_id"] = new_conversation_id
                             logger.info(f"Persisted final conversation_id: {new_conversation_id} for chat {update.effective_chat.id}")
                        break # Exit loop

                    elif event_type == "error":
                        error_msg = data.get('message', 'Unknown streaming error')
                        status_code = data.get('status', 'N/A')
                        logger.error(f"Error in stream: {status_code} - {error_msg}")
                        await edit_message_safe(context, update.effective_chat.id, placeholder_message_id, f"An error occurred during generation: {error_msg}")
                        # Persist conversation ID even if there was an error during generation
                        if new_conversation_id and new_conversation_id != context.chat_data.get("conversation_id"):
                             context.chat_data["conversation_id"] = new_conversation_id
                             logger.info(f"Persisted conversation_id: {new_conversation_id} after stream error for chat {update.effective_chat.id}")
                        return # Stop processing

                    elif event_type == "ping":
                        pass

                except json.JSONDecodeError:
                    logger.error(f"Failed to decode JSON from stream: {data_str}")
                except Exception as e:
                    logger.error(f"Error processing stream data: {e}", exc_info=True)
                    await edit_message_safe(context, update.effective_chat.id, placeholder_message_id, f"Error processing response: {type(e).__name__}")
                    # Persist conversation ID on processing error
                    if new_conversation_id and new_conversation_id != context.chat_data.get("conversation_id"):
                        context.chat_data["conversation_id"] = new_conversation_id
                        logger.info(f"Persisted conversation_id: {new_conversation_id} after processing error for chat {update.effective_chat.id}")
                    return

        # Ensure final message is updated if loop finished without message_end
        await edit_message_safe(context, update.effective_chat.id, placeholder_message_id, full_answer)

        # Store the latest conversation ID if it changed and wasn't stored mid-stream
        # (This check might be redundant due to mid-stream updates, but safe to keep)
        if new_conversation_id and new_conversation_id != context.chat_data.get("conversation_id"):
            context.chat_data["conversation_id"] = new_conversation_id
            logger.info(f"Stored final runtime conversation_id: {context.chat_data['conversation_id']} for chat {update.effective_chat.id}")
        elif not context.chat_data.get("conversation_id") and not new_conversation_id:
            logger.warning(f"No conversation ID received or stored for chat {update.effective_chat.id}")

    except httpx.StreamError as e:
        logger.error(f"Stream error while reading: {e}", exc_info=True)
        await edit_message_safe(context, update.effective_chat.id, placeholder_message_id, "Connection lost during response generation.")
        if new_conversation_id and new_conversation_id != context.chat_data.get("conversation_id"):
             context.chat_data["conversation_id"] = new_conversation_id
             logger.info(f"Persisted conversation_id: {new_conversation_id} after stream connection error for chat {update.effective_chat.id}")
    except Exception as e:
        logger.error(f"Unexpected error processing stream response: {e}", exc_info=True)
        await edit_message_safe(context, update.effective_chat.id, placeholder_message_id, f"An unexpected error occurred: {type(e).__name__}")
        if new_conversation_id and new_conversation_id != context.chat_data.get("conversation_id"):
             context.chat_data["conversation_id"] = new_conversation_id
             logger.info(f"Persisted conversation_id: {new_conversation_id} after unexpected error for chat {update.effective_chat.id}")


# --- Refactored Request and Processing Trigger ---
async def make_chat_api_request_and_process(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    query: str,
    # Removed user_id and conversation_id from args, get them from update/context
    placeholder_message_id: int
):
    """Makes request, handles stream context manager, and calls processor."""
    # *** Get user info and conversation ID here ***
    user = update.effective_user
    user_id = str(user.id)
    first_name = user.first_name
    username = user.username or "" # Handle case where username might be None
    conversation_id = context.chat_data.get("conversation_id") # Get from persisted data

    # *** Construct payload with user details ***
    payload = {
        "inputs": {},
        "query": query,
        "response_mode": "streaming",
        "user": user_id, # The user's unique ID
        "user_details": { # Add structured user info
            "first_name": first_name,
            "username": username,
            # You could add more like language_code: user.language_code
        },
        "files": [],
        "auto_generate_name": True
    }
    if conversation_id:
        payload["conversation_id"] = conversation_id

    # *** Modify Log message ***
    logger.info(
        f"Initiating Chat API stream request for user {user_id} "
        f"(Name: '{first_name}', Username: '@{username}' if username else 'N/A'), "
        f"Conversation: {conversation_id}"
    )
    error_message_to_report = None

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
             async with client.stream("POST", CHAT_ENDPOINT, headers=HEADERS, json=payload) as response:
                try:
                    response.raise_for_status()
                    # Pass the response handle to the processor
                    await process_stream_response(update, context, response, placeholder_message_id)

                except httpx.HTTPStatusError as e:
                    # ... (HTTP status error handling remains the same) ...
                    logger.error(f"HTTP Status Error from Chat API stream: {e.response.status_code} - {e.response.text}")
                    error_detail = "Unknown error."
                    try:
                        error_data = e.response.json()
                        error_detail = error_data.get('message', e.response.text)
                    except json.JSONDecodeError:
                        error_detail = e.response.text

                    if e.response.status_code == 401: error_detail = "Authentication failed (Check CHAT_API_KEY)."
                    elif e.response.status_code == 404: error_detail = f"API endpoint not found ({CHAT_ENDPOINT})."
                    elif e.response.status_code == 429: error_detail = "Rate limit exceeded."
                    elif e.response.status_code >= 500: error_detail = "Chat service internal error."

                    error_message_to_report = f"Sorry, there was an error from the chat service: {e.response.status_code} ({error_detail})"
                    await edit_message_safe(context, update.effective_chat.id, placeholder_message_id, error_message_to_report)


    except httpx.RequestError as e:
        logger.error(f"HTTP Request Error connecting to Chat API ({CHAT_ENDPOINT}): {e}")
        error_message_to_report = f"Sorry, I couldn't connect to the chat service ({type(e).__name__}). Please try again later."
        await edit_message_safe(context, update.effective_chat.id, placeholder_message_id, error_message_to_report)
    except Exception as e:
        logger.error(f"Unexpected error during Chat API request setup: {e}", exc_info=True)
        error_message_to_report = f"An unexpected error occurred: {type(e).__name__}"
        await edit_message_safe(context, update.effective_chat.id, placeholder_message_id, error_message_to_report)

    # Clean up the edit cache for this message ID after processing is done
    last_text_key = f"last_edit_{update.effective_chat.id}_{placeholder_message_id}"
    context.bot_data.pop(last_text_key, None)


# --- Telegram Command Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    user = update.effective_user
    # Clear conversation ID from *persistent* chat_data
    context.chat_data.pop("conversation_id", None)
    logger.info(f"User {user.id} ({user.first_name} @{user.username}) started bot. Cleared persistent conversation state for chat {update.effective_chat.id}.")
    await update.message.reply_html(
        rf"Hi {user.mention_html()}! 👋",
        reply_markup=ReplyKeyboardRemove(),
    )
    await update.message.reply_text(
        "I'm ready to chat! Send me a message.\n"
        "Use /new to start a fresh conversation (clears current chat memory)."
    )


async def new_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clears the persistent conversation ID for the current chat."""
    user = update.effective_user
    # Clear conversation ID from *persistent* chat_data
    context.chat_data.pop("conversation_id", None)
    logger.info(f"User {user.id} ({user.first_name} @{user.username}) requested new conversation in chat {update.effective_chat.id}. Persistent state cleared.")
    await update.message.reply_text("Okay, let's start a new conversation! Your previous chat history for this session will be ignored.")


# --- Updated handle_message ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles incoming text messages and interacts with the Chat API."""
    if not update.message or not update.message.text:
        return

    user = update.effective_user
    chat_id = update.effective_chat.id
    query = update.message.text
    # conversation_id is now fetched inside make_chat_api_request_and_process from context.chat_data

    logger.info(
        f"Received message from {user.id} ({user.first_name} @{user.username}) in chat {chat_id}. "
        f"Current Persistent Conversation ID: {context.chat_data.get('conversation_id')}"
    )

    placeholder_msg = await update.message.reply_text("🧠 Thinking...", reply_to_message_id=update.message.message_id)
    # Clear edit cache for the new placeholder message ID
    context.bot_data.pop(f"last_edit_{chat_id}_{placeholder_msg.message_id}", None)

    # Call the combined request and processing function
    # Pass only update, context, query, and message_id
    await make_chat_api_request_and_process(
        update, context, query, placeholder_msg.message_id
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log Errors caused by Updates."""
    logger.error(f"Update {update} caused error {context.error}", exc_info=context.error)
    # Optionally, notify the user if it's a user-facing error
    # if isinstance(context.error, SomeUserFacingError):
    #     if isinstance(update, Update) and update.effective_message:
    #         await update.effective_message.reply_text("Sorry, something went wrong processing your request.")


# --- Main Bot Execution ---

def main() -> None:
    """Start the bot with persistence."""
    # *** Setup Persistence ***
    # Ensure the directory for the persistence file exists
    persistence_dir = os.path.dirname(PERSISTENCE_PATH)
    try:
        os.makedirs(persistence_dir, exist_ok=True)
        logger.info(f"Persistence directory '{persistence_dir}' checked/created.")
    except OSError as e:
        logger.error(f"Could not create persistence directory '{persistence_dir}': {e}", exc_info=True)
        # Depending on severity, you might want to exit or continue without persistence
        raise # Reraise the error to stop startup if dir creation fails

    # Create the persistence object
    persistence = PicklePersistence(filepath=PERSISTENCE_PATH)
    logger.info(f"Using PicklePersistence at: {PERSISTENCE_PATH}")


    # Explicitly create JobQueue - PicklePersistence manages job persistence too
    # No need to manually create job_queue if using ApplicationBuilder with persistence
    # job_queue = JobQueue() # Removed, builder handles it with persistence

    # Pass the persistence object to the application builder
    application = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .persistence(persistence) # Use the configured persistence
        # .job_queue(job_queue) # Removed, builder handles it
        .build()
    )

    # --- Register Handlers ---
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("new", new_conversation))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)

    # Run the bot
    logger.info("Starting bot polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()