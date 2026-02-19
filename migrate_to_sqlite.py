"""
Migration script: JSON -> SQLite
Reads users.json and chat.json and populates the SQLite database.
Run once on the server before switching to the new code.
"""

import json
import os
import sys
import sqlite3
from datetime import datetime

# Use same DB file as users.py
DB_FILE = os.getenv('USERS_DB', 'users.db')
USERS_JSON = os.getenv('USERS_FILE', 'users.json')
CHAT_JSON = os.getenv('CHAT_FILE', 'chat.json')


def migrate():
    if os.path.exists(DB_FILE):
        print(f"Database {DB_FILE} already exists. Remove it first if you want to re-migrate.")
        response = input("Delete existing DB and re-migrate? (y/N): ").strip().lower()
        if response != 'y':
            print("Aborted.")
            sys.exit(0)
        os.remove(DB_FILE)
        print(f"Removed existing {DB_FILE}")

    # Import users module to create schema
    import users
    users.init_db()

    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    # Migrate users
    if os.path.exists(USERS_JSON):
        with open(USERS_JSON, 'r') as f:
            users_data = json.load(f)

        user_count = 0
        room_count = 0
        msg_count = 0

        for username, data in users_data.items():
            is_premium = 1 if data.get('is_premium', False) else 0
            points = data.get('points', 0)
            created_at = data.get('created_at', None)
            last_active = data.get('last_active', None)

            conn.execute(
                "INSERT OR IGNORE INTO users (username, is_premium, points, created_at, last_active) VALUES (?, ?, ?, ?, ?)",
                (username, is_premium, points, created_at, last_active)
            )
            user_count += 1

            # Migrate rooms
            rooms = data.get('rooms', [])
            for room in rooms:
                conn.execute(
                    "INSERT OR IGNORE INTO user_rooms (username, room) VALUES (?, ?)",
                    (username, room.upper())
                )
                room_count += 1

            # Migrate pending messages
            messages = data.get('messages', [])
            for msg in messages:
                conn.execute(
                    "INSERT INTO messages (username, type, title, text) VALUES (?, ?, ?, ?)",
                    (username, msg.get('type', 'success'), msg.get('title', ''), msg.get('text', ''))
                )
                msg_count += 1

            # If user has last_active, seed daily_usage for that date
            if last_active:
                usage_date = last_active[:10]  # Extract date part (YYYY-MM-DD)
                conn.execute(
                    "INSERT OR IGNORE INTO daily_usage (username, date) VALUES (?, ?)",
                    (username, usage_date)
                )

        conn.commit()
        print(f"Migrated {user_count} users, {room_count} room associations, {msg_count} pending messages")
    else:
        print(f"No {USERS_JSON} file found, skipping user migration")

    # Migrate chat messages
    if os.path.exists(CHAT_JSON):
        with open(CHAT_JSON, 'r') as f:
            chat_data = json.load(f)

        chat_count = 0
        for msg in chat_data:
            conn.execute(
                "INSERT INTO chat_messages (username, message, is_premium, timestamp) VALUES (?, ?, ?, ?)",
                (msg['username'], msg['message'], 1 if msg.get('is_premium', False) else 0, msg['timestamp'])
            )
            chat_count += 1

        conn.commit()
        print(f"Migrated {chat_count} chat messages")
    else:
        print(f"No {CHAT_JSON} file found, skipping chat migration")

    conn.close()
    print(f"\nMigration complete! Database: {DB_FILE}")
    print("You can now start the server with the new SQLite-backed users.py")


if __name__ == '__main__':
    migrate()
