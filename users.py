"""
Users module - manages user preferences, premium status, and favorite rooms.
Uses SQLite database for storage.
"""

import sqlite3
import json
import os
import logging
import base64
from datetime import datetime, date
from typing import Optional, List
from contextlib import contextmanager

DB_FILE = os.getenv('USERS_DB', 'users.db')


@contextmanager
def _get_db():
    """Context manager for database connections."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Initialize the database schema."""
    with _get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                is_premium INTEGER NOT NULL DEFAULT 0,
                points INTEGER NOT NULL DEFAULT 0,
                created_at TEXT,
                last_active TEXT,
                job_title TEXT,
                office_location TEXT
            );

            CREATE TABLE IF NOT EXISTS user_rooms (
                username TEXT NOT NULL,
                room TEXT NOT NULL,
                PRIMARY KEY (username, room),
                FOREIGN KEY (username) REFERENCES users(username)
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                text TEXT NOT NULL,
                FOREIGN KEY (username) REFERENCES users(username)
            );

            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                message TEXT NOT NULL,
                is_premium INTEGER NOT NULL DEFAULT 0,
                timestamp TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS daily_usage (
                username TEXT NOT NULL,
                date TEXT NOT NULL,
                PRIMARY KEY (username, date),
                FOREIGN KEY (username) REFERENCES users(username)
            );

            CREATE TABLE IF NOT EXISTS room_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_number TEXT NOT NULL,
                username TEXT NOT NULL,
                date TEXT NOT NULL,
                UNIQUE(room_number, username, date)
            );
        """)
    logging.info("Database initialized")

    # Migrate: add new columns if they don't exist
    with _get_db() as conn:
        existing_cols = [col[1] for col in conn.execute("PRAGMA table_info(users)").fetchall()]
        if "job_title" not in existing_cols:
            conn.execute("ALTER TABLE users ADD COLUMN job_title TEXT")
            logging.info("Added job_title column to users table")
        if "office_location" not in existing_cols:
            conn.execute("ALTER TABLE users ADD COLUMN office_location TEXT")
            logging.info("Added office_location column to users table")

        # Migrate room_stats: replace action column with username column
        room_stats_cols = [col[1] for col in conn.execute("PRAGMA table_info(room_stats)").fetchall()]
        if "action" in room_stats_cols and "username" not in room_stats_cols:
            conn.execute("DROP TABLE room_stats")
            conn.execute("""
                CREATE TABLE room_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    room_number TEXT NOT NULL,
                    username TEXT NOT NULL,
                    date TEXT NOT NULL,
                    UNIQUE(room_number, username, date)
                )
            """)
            logging.info("Migrated room_stats table: replaced action with username")


# Initialize DB on module import
init_db()


# ============== User Functions ==============

def get_or_create_user(username: str, job_title: str = None, office_location: str = None) -> dict:
    """Get existing user or create a new one. Returns user dict."""
    now = datetime.now().isoformat()
    with _get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO users (username, is_premium, points, created_at, last_active, job_title, office_location) VALUES (?, 0, 0, ?, ?, ?, ?)",
                (username, now, now, job_title, office_location)
            )
            logging.info(f"Created new user: {username}")
            return {
                "username": username,
                "is_premium": False,
                "rooms": [],
                "points": 0,
                "created_at": now,
                "last_active": now,
                "job_title": job_title,
                "office_location": office_location
            }
        # Update job_title and office_location if provided
        if job_title is not None or office_location is not None:
            updates = []
            params = []
            if job_title is not None:
                updates.append("job_title = ?")
                params.append(job_title)
            if office_location is not None:
                updates.append("office_location = ?")
                params.append(office_location)
            params.append(username)
            conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE username = ?", params)
        rooms = [r["room"] for r in conn.execute("SELECT room FROM user_rooms WHERE username = ?", (username,)).fetchall()]
        return {
            "username": row["username"],
            "is_premium": bool(row["is_premium"]),
            "rooms": rooms,
            "points": row["points"],
            "created_at": row["created_at"],
            "last_active": row["last_active"],
            "job_title": job_title or row["job_title"],
            "office_location": office_location or row["office_location"]
        }


def get_user(username: str) -> Optional[dict]:
    """Get user by username. Returns None if not found."""
    with _get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if not row:
            return None
        rooms = [r["room"] for r in conn.execute("SELECT room FROM user_rooms WHERE username = ?", (username,)).fetchall()]
        return {
            "username": row["username"],
            "is_premium": bool(row["is_premium"]),
            "rooms": rooms,
            "points": row["points"],
            "created_at": row["created_at"],
            "last_active": row["last_active"],
            "job_title": row["job_title"],
            "office_location": row["office_location"]
        }


def user_exists(username: str) -> bool:
    """Check if user exists in the system."""
    with _get_db() as conn:
        row = conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
        return row is not None


def is_premium(username: str) -> bool:
    """Check if user has premium status."""
    with _get_db() as conn:
        row = conn.execute("SELECT is_premium FROM users WHERE username = ?", (username,)).fetchone()
        return bool(row["is_premium"]) if row else False


def set_premium(username: str, value: bool = True):
    """Set user's premium status."""
    with _get_db() as conn:
        conn.execute("UPDATE users SET is_premium = ? WHERE username = ?", (int(value), username))
        logging.info(f"Set premium={value} for user: {username}")


def add_room(username: str, room: str):
    """Add a room to user's controlled rooms list (if not already there)."""
    room = room.upper()
    with _get_db() as conn:
        # Ensure user exists
        row = conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO users (username, is_premium, points) VALUES (?, 0, 0)",
                (username,)
            )
        conn.execute(
            "INSERT OR IGNORE INTO user_rooms (username, room) VALUES (?, ?)",
            (username, room)
        )
        logging.info(f"Added room {room} to user {username}")


def get_rooms(username: str) -> List[str]:
    """Get list of rooms user has controlled."""
    with _get_db() as conn:
        rows = conn.execute("SELECT room FROM user_rooms WHERE username = ?", (username,)).fetchall()
        return [r["room"] for r in rows]


def get_all_users() -> dict:
    """Get all users (for admin purposes). Returns dict keyed by username."""
    with _get_db() as conn:
        users = {}
        for row in conn.execute("SELECT * FROM users").fetchall():
            username = row["username"]
            rooms = [r["room"] for r in conn.execute("SELECT room FROM user_rooms WHERE username = ?", (username,)).fetchall()]

            # Count total unique days used
            usage_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM daily_usage WHERE username = ?",
                (username,)
            ).fetchone()["cnt"]

            users[username] = {
                "is_premium": bool(row["is_premium"]),
                "rooms": rooms,
                "points": row["points"],
                "created_at": row["created_at"],
                "last_active": row["last_active"],
                "job_title": row["job_title"],
                "office_location": row["office_location"],
                "usage_days": usage_count
            }
        return users


def update_last_active(username: str):
    """Update the last_active timestamp and record daily usage."""
    now = datetime.now().isoformat()
    today = date.today().isoformat()
    with _get_db() as conn:
        conn.execute("UPDATE users SET last_active = ? WHERE username = ?", (now, username))
        # Record daily usage
        conn.execute(
            "INSERT OR IGNORE INTO daily_usage (username, date) VALUES (?, ?)",
            (username, today)
        )


def get_users_active_today() -> List[dict]:
    """Get list of users who were active today with their rooms."""
    today = date.today().isoformat()
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT u.* FROM users u JOIN daily_usage d ON u.username = d.username WHERE d.date = ? ORDER BY u.last_active DESC",
            (today,)
        ).fetchall()
        result = []
        for row in rows:
            rooms = [r["room"] for r in conn.execute("SELECT room FROM user_rooms WHERE username = ?", (row["username"],)).fetchall()]
            result.append({
                "username": row["username"],
                "rooms": rooms,
                "is_premium": bool(row["is_premium"]),
                "last_active": row["last_active"]
            })
        return result


def get_new_users_today() -> List[dict]:
    """Get list of users who registered today (first time on site)."""
    today = date.today().isoformat()
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM users WHERE created_at LIKE ? ORDER BY created_at DESC",
            (today + "%",)
        ).fetchall()
        result = []
        for row in rows:
            rooms = [r["room"] for r in conn.execute("SELECT room FROM user_rooms WHERE username = ?", (row["username"],)).fetchall()]
            result.append({
                "username": row["username"],
                "rooms": rooms,
                "is_premium": bool(row["is_premium"]),
                "created_at": row["created_at"]
            })
        return result


# ============== Referral Functions ==============

def get_referral_code(username: str) -> str:
    """Generate a simple referral code from username (base64 encoded)."""
    return base64.urlsafe_b64encode(username.encode()).decode().rstrip('=')


def get_username_from_referral(code: str) -> Optional[str]:
    """Decode a referral code back to username."""
    try:
        padding = 4 - len(code) % 4
        if padding != 4:
            code += '=' * padding
        return base64.urlsafe_b64decode(code.encode()).decode()
    except Exception:
        return None


def process_referral(referrer_username: str, new_user: str) -> bool:
    """Process a referral. Returns True if successful."""
    with _get_db() as conn:
        row = conn.execute("SELECT 1 FROM users WHERE username = ?", (referrer_username,)).fetchone()
        if row:
            logging.info(f"Processed referral for {referrer_username} from {new_user}")
            return True
    return False


# ============== Message Functions ==============

def add_message(username: str, message_type: str, title: str, text: str):
    """Add a message to user's message queue."""
    with _get_db() as conn:
        # Ensure user exists
        row = conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO users (username, is_premium, points) VALUES (?, 0, 0)",
                (username,)
            )
            logging.info(f"Created new user {username} while adding message")
        conn.execute(
            "INSERT INTO messages (username, type, title, text) VALUES (?, ?, ?, ?)",
            (username, message_type, title, text)
        )
    logging.info(f"Added {message_type} message to {username}: '{title}'")


def get_and_clear_messages(username: str) -> list:
    """Get all pending messages for user and clear them."""
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT type, title, text FROM messages WHERE username = ?",
            (username,)
        ).fetchall()
        messages = [{"type": r["type"], "title": r["title"], "text": r["text"]} for r in rows]
        logging.info(f"get_and_clear_messages for {username}: Found {len(messages)} messages")
        if messages:
            logging.info(f"Messages for {username}: {messages}")
            conn.execute("DELETE FROM messages WHERE username = ?", (username,))
            logging.info(f"Cleared {len(messages)} messages for {username}")
        return messages


# ============== Points Functions ==============

def get_points(username: str) -> int:
    """Get user's current points."""
    with _get_db() as conn:
        row = conn.execute("SELECT points FROM users WHERE username = ?", (username,)).fetchone()
        return row["points"] if row else 0


def add_points(username: str, points: int):
    """Add points to user's balance. Auto-grant premium at 60 points."""
    with _get_db() as conn:
        row = conn.execute("SELECT points, is_premium FROM users WHERE username = ?", (username,)).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO users (username, is_premium, points) VALUES (?, 0, ?)",
                (username, points)
            )
            new_total = points
            was_premium = False
        else:
            new_total = row["points"] + points
            was_premium = bool(row["is_premium"])
            conn.execute("UPDATE users SET points = ? WHERE username = ?", (new_total, username))

        # Auto-grant premium at 60 points
        if new_total >= 60 and not was_premium:
            conn.execute("UPDATE users SET is_premium = 1 WHERE username = ?", (username,))
            logging.info(f"Auto-granted premium to {username} for reaching 60 points")

    logging.info(f"Added {points} points to {username}, new total: {new_total}")


# ============== Chat Functions ==============

def add_chat_message(username: str, message: str, is_premium: bool = False):
    """Add a chat message."""
    now = datetime.now().isoformat()
    with _get_db() as conn:
        conn.execute(
            "INSERT INTO chat_messages (username, message, is_premium, timestamp) VALUES (?, ?, ?, ?)",
            (username, message, int(is_premium), now)
        )
        # Keep only last 100 messages
        conn.execute("""
            DELETE FROM chat_messages WHERE id NOT IN (
                SELECT id FROM chat_messages ORDER BY id DESC LIMIT 100
            )
        """)
    logging.info(f"Added chat message from {username} (premium: {is_premium})")


def get_chat_messages() -> list:
    """Get all chat messages."""
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT username, message, is_premium, timestamp FROM chat_messages ORDER BY id ASC"
        ).fetchall()
        return [
            {
                "username": r["username"],
                "message": r["message"],
                "is_premium": bool(r["is_premium"]),
                "timestamp": r["timestamp"]
            }
            for r in rows
        ]


# ============== User Search Functions ==============

def search_users(query: str) -> List[str]:
    """Search for usernames starting with the given query (min 3 chars)."""
    if len(query) < 3:
        return []
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT username FROM users WHERE username LIKE ? ORDER BY username LIMIT 20",
            (query + "%",)
        ).fetchall()
        return [r["username"] for r in rows]


# ============== Room Statistics Functions ==============

def record_room_stat(room_number: str, username: str):
    """Record that a user controlled a room today."""
    today = date.today().isoformat()
    with _get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO room_stats (room_number, username, date) VALUES (?, ?, ?)",
            (room_number, username, today)
        )
    logging.info(f"Recorded stat: room={room_number}, user={username}")


def get_daily_room_stats(target_date: str = None) -> list:
    """Get room statistics for a specific date (default: today) with unique user counts."""
    if target_date is None:
        target_date = date.today().isoformat()
    with _get_db() as conn:
        rows = conn.execute("""
            SELECT room_number,
                   COUNT(DISTINCT username) as unique_users
            FROM room_stats WHERE date = ?
            GROUP BY room_number ORDER BY room_number
        """, (target_date,)).fetchall()
        return [{"room_number": r["room_number"], "unique_users": r["unique_users"]} for r in rows]


def get_unique_rooms_today() -> int:
    """Count unique rooms used today."""
    today = date.today().isoformat()
    with _get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(DISTINCT room_number) as cnt FROM room_stats WHERE date = ?",
            (today,)
        ).fetchone()
        return row["cnt"] if row else 0
