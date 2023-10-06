import json
import os
from typing import Dict

import psycopg2
from dotenv import load_dotenv

load_dotenv("app/.env")

db_host = os.environ.get("PGHOST")
db_user = os.environ.get("PGUSER")
db_password = os.environ.get("PGPASSWORD")
db_name = os.environ.get("PGDATABASE")

def create_db():
    # Create a connection to the database
    with psycopg2.connect(
        host=db_host, user=db_user, password=db_password, database=db_name
    ) as conn:
        c = conn.cursor()
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_id TEXT PRIMARY KEY,
                enquiries INTEGER,
                current_language TEXT
            );
             """
        )
        conn.commit()

def delete_db():
    # Create a connection to the database
    with psycopg2.connect(
        host=db_host, user=db_user, password=db_password, database=db_name
    ) as conn:
        c = conn.cursor()
        c.execute("DROP TABLE IF EXISTS users;")
        conn.commit()

def add_new_user(user: str, language: str = "en"):
    new_user = {"telegram_id": user, "enquiries": 0, "current_language": language}

    with psycopg2.connect(
        host=db_host, user=db_user, password=db_password, database=db_name
    ) as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO users (telegram_id, enquiries, current_language) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING;",
            (new_user["telegram_id"], new_user["enquiries"], new_user["current_language"]),
        )
        conn.commit()


def update_enquiry_user(telegram_id):
    try:
        # Create a connection to the database
        with psycopg2.connect(
                host=db_host, user=db_user, password=db_password, database=db_name
        ) as conn:
            c = conn.cursor()

            # Increment the user's enquiries by 1
            c.execute(
                """
                UPDATE users
                SET enquiries = enquiries + 1
                WHERE telegram_id = %s
                """,
                (telegram_id,)
            )
            conn.commit()

    except psycopg2.Error as e:
        print(f"Error updating user: {e}")


def set_user_language(telegram_id, current_language):
    try:
        # Create a connection to the database
        with psycopg2.connect(
                host=db_host, user=db_user, password=db_password, database=db_name
        ) as conn:
            c = conn.cursor()

            # Update the user's current_language
            c.execute(
                """
                UPDATE users
                SET current_language = %s
                WHERE telegram_id = %s
                """,
                (current_language, telegram_id)
            )
            conn.commit()

    except psycopg2.Error as e:
        print(f"Error setting user language: {e}")


def get_user_language(telegram_id):
    try:
        # Create a connection to the database
        with psycopg2.connect(
                host=db_host, user=db_user, password=db_password, database=db_name
        ) as conn:
            c = conn.cursor()

            # Retrieve the user's current_language
            c.execute(
                """
                SELECT current_language
                FROM users
                WHERE telegram_id = %s
                """,
                (telegram_id,)
            )
            result = c.fetchone()

            if result:
                return result[0]  # Return the current_language
            else:
                return None  # User not found

    except psycopg2.Error as e:
        print(f"Error getting user language: {e}")

if __name__ == "__main__":
    #delete_db()

    create_db()
    user = "323232"
    #add_new_user(user)

    #question = "What's the meaning of life?"
    #answer = "42"

    #update_enquiry_user(user)

    set_user_language(user, 'ar')
    print(get_user_language(user))
    #print("after update: ", row)

    #reset_history_user(user)
    #print("\n" * 4)

    #row = retrieve_history(user)
    #print("After reset: ", row)
