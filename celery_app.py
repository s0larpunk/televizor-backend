import os
from celery import Celery
from dotenv import load_dotenv

load_dotenv()

# Default to local Redis
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "telegram_feed",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["tasks"]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Rate limit tasks to avoid flooding Telegram API if queue builds up
    task_default_rate_limit="20/s", 
)
