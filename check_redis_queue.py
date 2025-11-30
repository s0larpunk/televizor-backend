import redis
import os
from dotenv import load_dotenv

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
r = redis.from_url(REDIS_URL)

queue_len = r.llen("celery")
print(f"Celery queue length: {queue_len}")

# List first few items
items = r.lrange("celery", 0, 4)
for item in items:
    print(f"Task: {item}")
