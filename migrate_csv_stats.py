"""
One-time migration: Import existing CSV statistics into SQLite room_stats table.
Run this on the server after deploying the new code.
"""
import os
import csv
import sqlite3
from datetime import datetime

DB_FILE = os.getenv('USERS_DB', 'users.db')
STATS_DIR = 'stats'


def migrate():
    if not os.path.exists(STATS_DIR):
        print("No stats directory found, nothing to migrate.")
        return

    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA journal_mode=WAL")

    # Ensure table exists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS room_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_number TEXT NOT NULL,
            action TEXT NOT NULL,
            date TEXT NOT NULL
        )
    """)

    total = 0
    stats_files = sorted(os.listdir(STATS_DIR))

    for filename in stats_files:
        if not (filename.startswith('stats_') and filename.endswith('.csv')):
            continue

        date_str = filename[6:-4]  # Extract YYYY-MM-DD from stats_YYYY-MM-DD.csv
        filepath = os.path.join(STATS_DIR, filename)

        try:
            with open(filepath, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    room = row.get('room_number', '')
                    if not room:
                        continue
                    # Insert individual action records based on counts
                    for action in ['up', 'down', 'stop']:
                        count = int(row.get(action, 0))
                        for _ in range(count):
                            conn.execute(
                                "INSERT INTO room_stats (room_number, action, date) VALUES (?, ?, ?)",
                                (room, action, date_str)
                            )
                            total += 1
            print(f"  Migrated {filename}")
        except Exception as e:
            print(f"  Error migrating {filename}: {e}")

    conn.commit()
    conn.close()
    print(f"\nDone! Migrated {total} stat records from CSV to SQLite.")


if __name__ == '__main__':
    migrate()
