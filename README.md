# Issy Bot - Telegram Chat API Bridge

A Dockerized Telegram bot acting as a bridge to a backend chat API service. Features streaming responses and persistent conversation state via a Docker volume.

## Setup

1.  **Clone:**
    ```bash
    git clone https://github.com/7azmi/issy-bot.git
    cd issy-bot
    ```

2.  **Configure Environment:**
    *   Copy the example environment file:
        ```bash
        cp .env.example .env
        ```
    *   Edit `.env` and provide your actual credentials:
        *   `TELEGRAM_BOT_TOKEN`
        *   `CHAT_API_KEY`
        *   `CHAT_API_BASE_URL`

## Deployment (Docker)

1.  **Build & Run:**
    ```bash
    # Build/rebuild image and run detached
    docker compose up --build -d
    ```

2.  **Logs:**
    ```bash
    docker compose logs -f
    ```

3.  **Stop:**
    ```bash
    docker compose down
    ```

## Persistence

*   Conversation state (`chat_data`) is persisted using `PicklePersistence`.
*   Data is stored in the named Docker volume `bot_data`, mapped to `/data/bot_persistence.pkl` within the container.

## Set Telegram Commands (Optional)

*   To update the command list visible in Telegram (run once or after changes):
    ```bash
    docker compose run --rm telegram-bot python set_commands.py
    ```

---

*Refer to `.env.example` for required environment variable names.*