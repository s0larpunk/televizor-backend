import sqlite3
import os
import sys

# Add parent directory to path to import config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

DB_PATH = "telegram_feed.db"

def migrate():
    if not os.path.exists(DB_PATH):
        print(f"Database {DB_PATH} not found.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        # Check if user_sessions table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user_sessions'")
        if cursor.fetchone():
            print("user_sessions table already exists.")
        else:
            print("Creating user_sessions table...")
            cursor.execute("""
                CREATE TABLE user_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_phone VARCHAR NOT NULL,
                    session_string VARCHAR NOT NULL,
                    instance_id VARCHAR NOT NULL DEFAULT 'default',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_used_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_phone) REFERENCES users (phone),
                    UNIQUE (user_phone, instance_id)
                )
            """)
            print("user_sessions table created.")

            # Migrate existing sessions
            print("Migrating existing sessions...")
            cursor.execute("SELECT phone, session_string FROM users WHERE session_string IS NOT NULL")
            users = cursor.fetchall()
            
            count = 0
            for phone, session_string in users:
                if session_string:
                    cursor.execute("""
                        INSERT INTO user_sessions (user_phone, session_string, instance_id)
                        VALUES (?, ?, 'default')
                    """, (phone, session_string))
                    count += 1
            
            print(f"Migrated {count} sessions.")

        conn.commit()
        print("Migration completed successfully.")
    except Exception as e:
        print(f"Migration failed: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    migrate()
