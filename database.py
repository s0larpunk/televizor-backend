from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from dotenv import load_dotenv

import config

load_dotenv()

# Default to SQLite for local development if DATABASE_URL is not set
# Use DATABASE_URL from Railway (PostgreSQL), fallback to SQLite for local dev
DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL:
    # Railway PostgreSQL - fix the URL format (Railway uses postgres://, SQLAlchemy needs postgresql://)
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    
    # Create PostgreSQL engine
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,  # Verify connections before using them
        pool_recycle=300,    # Recycle connections after 5 minutes
    )
    print(f"✓ Connected to PostgreSQL database")
else:
    # Local development - use SQLite
    os.makedirs("data", exist_ok=True)
    SQLALCHEMY_DATABASE_URL = "sqlite:///./data/telegram_feed.db"
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL,
        connect_args={"check_same_thread": False}
    )
    print(f"✓ Using local SQLite database: {SQLALCHEMY_DATABASE_URL}")

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
