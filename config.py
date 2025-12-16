import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Telegram API Configuration
TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")

# Session Configuration
SESSION_DIR = Path(os.getenv("SESSION_DIR", "./sessions"))
SESSION_DIR.mkdir(exist_ok=True)

# Database Configuration
# Check if /app/data exists (Railway Volume)
if os.path.exists("/app/data"):
    DB_PATH = "/app/data/telegram_feed.db"
# Check if local data directory exists
elif os.path.exists("data"):
    DB_PATH = "data/telegram_feed.db"
else:
    DB_PATH = "telegram_feed.db"

# Server Configuration
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

# Auto-detect local environment
INSTANCE_ID = os.getenv("INSTANCE_ID")
if not INSTANCE_ID:
    if "localhost" in FRONTEND_URL or "127.0.0.1" in FRONTEND_URL:
        INSTANCE_ID = "local"
    else:
        INSTANCE_ID = "default"

# Feed Configuration Storage
FEEDS_CONFIG_FILE = Path("./feeds_config.json")

# Payment Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")  # Your bot token from @BotFather
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")  # Random secret for webhook validation
PREMIUM_PRICE_STARS = int(os.getenv("PREMIUM_PRICE_STARS", "100"))  # Price in Telegram Stars

# Stripe Configuration
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

# T-Bank Configuration
TBANK_TERMINAL_KEY = os.getenv("TBANK_TERMINAL_KEY", "")
TBANK_PASSWORD = os.getenv("TBANK_PASSWORD", "")
TBANK_TEST_MODE = os.getenv("TBANK_TEST_MODE", "true").lower() == "true"

# Coinbase Configuration
COINBASE_API_KEY = os.getenv("COINBASE_API_KEY", "")
COINBASE_WEBHOOK_SECRET = os.getenv("COINBASE_WEBHOOK_SECRET", "")

# Admin Notification
ADMIN_PHONE = os.getenv("ADMIN_PHONE", "+33759863632")
