import logging
import os
import json
import httpx
import asyncio
from dotenv import load_dotenv
import pytz # Keep pytz import

from telegram import Update, constants, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    JobQueue
)
from telegram.error import RetryAfter, TimedOut

# --- Configuration ---
load_dotenv() # Load environment variables from .env file

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_API_KEY = os.getenv("CHAT_API_KEY")
CHAT_API_BASE_URL = os.getenv("CHAT_API_BASE_URL")

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
        last_text_key = f"last_text_{message_id}"
        if context.chat_data.get(last_text_key) != text:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text or "...", # Ensure non-empty text
                parse_mode=constants.ParseMode.MARKDOWN
            )
            context.chat_data[last_text_key] = text # Store the last edited text
    except RetryAfter as e:
        logger.warning(f"Rate limited while editing message: Sleeping for {e.retry_after}s")
        await asyncio.sleep(e.retry_after)
        await edit_message_safe(context, chat_id, message_id, text) # Retry after waiting
    except TimedOut:
        logger.warning("Timed out while editing message. Skipping.")
    except Exception as e:
        if "Message is not modified" in str(e):
             logger.debug(f"Message {message_id} not modified, skipping edit.")
             context.chat_data[last_text_key] = text # Still update our record
        else:
            logger.error(f"Failed to edit message {message_id}: {e}", exc_info=True)


async def process_stream_response(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    stream_response: httpx.Response, # Now receives the response handle directly
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
        # Iterate directly over the stream response's lines
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

                    elif event_type == "agent_thought":
                        pass

                    elif event_type == "message_end":
                        logger.info(f"Stream ended for conversation {new_conversation_id or current_conversation_id}")
                        await edit_message_safe(context, update.effective_chat.id, placeholder_message_id, full_answer)
                        break # Exit loop

                    elif event_type == "error":
                        error_msg = data.get('message', 'Unknown streaming error')
                        status_code = data.get('status', 'N/A')
                        logger.error(f"Error in stream: {status_code} - {error_msg}")
                        await edit_message_safe(context, update.effective_chat.id, placeholder_message_id, f"An error occurred during generation: {error_msg}")
                        if new_conversation_id:
                            context.chat_data["conversation_id"] = new_conversation_id
                        return # Stop processing

                    elif event_type == "ping":
                        pass

                except json.JSONDecodeError:
                    logger.error(f"Failed to decode JSON from stream: {data_str}")
                except Exception as e:
                    logger.error(f"Error processing stream data: {e}", exc_info=True)
                    await edit_message_safe(context, update.effective_chat.id, placeholder_message_id, f"Error processing response: {type(e).__name__}")
                    if new_conversation_id:
                        context.chat_data["conversation_id"] = new_conversation_id
                    return

        # Ensure final message is updated if loop finished without message_end
        await edit_message_safe(context, update.effective_chat.id, placeholder_message_id, full_answer)

        # Store the latest conversation ID in runtime chat_data
        if new_conversation_id:
            context.chat_data["conversation_id"] = new_conversation_id
            logger.info(f"Stored runtime conversation_id: {context.chat_data['conversation_id']} for chat {update.effective_chat.id}")
        elif not context.chat_data.get("conversation_id") and not new_conversation_id:
            logger.warning(f"No conversation ID received or stored for chat {update.effective_chat.id}")

    except httpx.StreamError as e:
        # Errors during the stream reading itself
        logger.error(f"Stream error while reading: {e}", exc_info=True)
        await edit_message_safe(context, update.effective_chat.id, placeholder_message_id, "Connection lost during response generation.")
        if new_conversation_id:
             context.chat_data["conversation_id"] = new_conversation_id
    except Exception as e:
        logger.error(f"Unexpected error processing stream response: {e}", exc_info=True)
        await edit_message_safe(context, update.effective_chat.id, placeholder_message_id, f"An unexpected error occurred: {type(e).__name__}")
        if new_conversation_id:
             context.chat_data["conversation_id"] = new_conversation_id
    # No finally block needed to close stream here, the context manager handles it


# --- Refactored Request and Processing Trigger ---
async def make_chat_api_request_and_process(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    query: str,
    user_id: str,
    conversation_id: str | None,
    placeholder_message_id: int
):
    """Makes request, handles stream context manager, and calls processor."""
    payload = {
        "inputs": {},
        "query": query,
        "response_mode": "streaming",
        "user": user_id,
        "files": [],
        "auto_generate_name": True
    }
    if conversation_id:
        payload["conversation_id"] = conversation_id

    logger.info(f"Initiating Chat API stream request for user {user_id}, conversation: {conversation_id}")
    error_message_to_report = None # Store potential errors to report back

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
             # *** CORRECTED USAGE OF client.stream ***
             async with client.stream("POST", CHAT_ENDPOINT, headers=HEADERS, json=payload) as response:
                try:
                    # Check status immediately after response headers are received
                    response.raise_for_status()
                    # Pass the response handle (valid within this context) to the processor
                    await process_stream_response(update, context, response, placeholder_message_id)

                except httpx.HTTPStatusError as e:
                    # Handle status errors found *after* stream started (e.g., 4xx/5xx during streaming)
                    logger.error(f"HTTP Status Error from Chat API stream: {e.response.status_code} - {e.response.text}")
                    error_detail = "Unknown error."
                    try:
                        error_data = e.response.json()
                        error_detail = error_data.get('message', e.response.text)
                    except json.JSONDecodeError:
                        error_detail = e.response.text

                    if e.response.status_code == 401: error_detail = "Authentication failed."
                    elif e.response.status_code == 429: error_detail = "Rate limit exceeded."
                    elif e.response.status_code >= 500: error_detail = "Chat service internal error."

                    error_message_to_report = f"Sorry, there was an error from the chat service: {e.response.status_code} ({error_detail})"
                    await edit_message_safe(context, update.effective_chat.id, placeholder_message_id, error_message_to_report)

                # Note: Exceptions within process_stream_response are caught there

    # Handle errors during the *initial* connection/request phase
    except httpx.RequestError as e:
        logger.error(f"HTTP Request Error connecting to Chat API: {e}")
        error_message_to_report = f"Sorry, I couldn't connect to the chat service ({type(e).__name__}). Please try again later."
        await edit_message_safe(context, update.effective_chat.id, placeholder_message_id, error_message_to_report)
    except Exception as e:
        # Catch any other unexpected errors during setup
        logger.error(f"Unexpected error during Chat API request setup: {e}", exc_info=True)
        error_message_to_report = f"An unexpected error occurred: {type(e).__name__}"
        await edit_message_safe(context, update.effective_chat.id, placeholder_message_id, error_message_to_report)

    # No return needed unless you want to signal the final status from handle_message


# --- Telegram Command Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    user = update.effective_user
    context.chat_data.pop("conversation_id", None)
    logger.info(f"User {user.id} ({user.username}) started bot. Cleared runtime conversation state.")
    await update.message.reply_html(
        rf"Hi {user.mention_html()}! 👋",
        reply_markup=ReplyKeyboardRemove(),
    )
    await update.message.reply_text(
        "I'm ready to chat! Send me a message.\n"
        "Use /new to start a fresh conversation (clears current chat memory)."
    )


async def new_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clears the runtime conversation ID for the current chat."""
    context.chat_data.pop("conversation_id", None)
    logger.info(f"User {update.effective_user.id} requested new conversation in chat {update.effective_chat.id}")
    await update.message.reply_text("Okay, let's start a new conversation! Your previous chat history for this session will be ignored.")


# --- Updated handle_message ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles incoming text messages and interacts with the Chat API."""
    if not update.message or not update.message.text:
        return

    user_id = str(update.effective_user.id)
    chat_id = update.effective_chat.id
    query = update.message.text
    conversation_id = context.chat_data.get("conversation_id")

    logger.info(f"Received message from {user_id} in chat {chat_id}. Runtime Conversation: {conversation_id}")

    placeholder_msg = await update.message.reply_text("🧠 Thinking...", reply_to_message_id=update.message.message_id)
    context.chat_data.pop(f"last_text_{placeholder_msg.message_id}", None) # Clear cache

    # Call the combined request and processing function
    # Error handling and message editing are now primarily managed within that function
    await make_chat_api_request_and_process(
        update, context, query, user_id, conversation_id, placeholder_msg.message_id
    )

    # No explicit error check needed here anymore, as errors should have been edited
    # into the placeholder message by the called function.


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log Errors caused by Updates."""
    logger.error(f"Update {update} caused error {context.error}", exc_info=context.error)


# --- Main Bot Execution ---

def main() -> None:
    """Start the bot."""
    # Explicitly create JobQueue
    job_queue = JobQueue()

    # Pass the manually created job_queue to the application builder
    application = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .job_queue(job_queue) # Use the created job_queue
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