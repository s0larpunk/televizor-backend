"""
Migration script to add payment_method column to users table
Run this with: python add_payment_method_column.py
"""

from sqlalchemy import create_engine, text
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./telegram_feed.db")

# Fix postgres:// to postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

def migrate():
    if DATABASE_URL.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
    else:
        connect_args = {}
    
    engine = create_engine(DATABASE_URL, connect_args=connect_args)
    
    with engine.connect() as conn:
        # For SQLite, check if column exists differently
        if DATABASE_URL.startswith("sqlite"):
            result = conn.execute(text("PRAGMA table_info(users)"))
            columns = [row[1] for row in result]
            
            if 'payment_method' in columns:
                print("Column 'payment_method' already exists. Skipping migration.")
                return
            
            # Add the column for SQLite
            conn.execute(text("ALTER TABLE users ADD COLUMN payment_method VARCHAR"))
        else:
            # PostgreSQL
            result = conn.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='users' AND column_name='payment_method'
            """))
            
            if result.fetchone():
                print("Column 'payment_method' already exists. Skipping migration.")
                return
            
            conn.execute(text("ALTER TABLE users ADD COLUMN payment_method VARCHAR"))
        
        conn.commit()
        
        print("✅ Successfully added 'payment_method' column to users table")

if __name__ == "__main__":
    migrate()
