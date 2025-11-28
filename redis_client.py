import redis
import os
import time
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Create Redis client
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

class RateLimiter:
    """Distributed rate limiter using Redis."""
    
    @staticmethod
    def check_rate_limit(user_id: str, key: str, max_hourly: int = None, max_daily: int = None) -> bool:
        """
        Check if action is allowed under rate limits.
        Returns True if allowed, False if limit exceeded.
        """
        if not max_hourly and not max_daily:
            return True
            
        now = int(time.time())
        pipe = redis_client.pipeline()
        
        # Keys for sliding windows (or simple buckets for efficiency)
        # Using simple hourly/daily buckets for simplicity and performance
        hour_key = f"rate:{user_id}:{key}:hour:{now // 3600}"
        day_key = f"rate:{user_id}:{key}:day:{now // 86400}"
        
        # Increment counters
        if max_hourly:
            pipe.incr(hour_key)
            pipe.expire(hour_key, 3600 * 2) # Expire after 2 hours
            
        if max_daily:
            pipe.incr(day_key)
            pipe.expire(day_key, 86400 * 2) # Expire after 2 days
            
        results = pipe.execute()
        
        # Check results
        idx = 0
        if max_hourly:
            current_hour_count = results[idx]
            if current_hour_count > max_hourly:
                return False
            idx += 2 # incr + expire
            
        if max_daily:
            current_day_count = results[idx]
            if current_day_count > max_daily:
                return False
                
        return True

    @staticmethod
    def record_message(user_id: str, key: str):
        """
        Record a message for rate limiting.
        Note: In this implementation, check_rate_limit already increments,
        so this might be redundant unless we want to separate check from increment.
        For now, we'll assume check_rate_limit is called BEFORE action, and it increments tentatively.
        If action fails, we could decrement, but for rate limiting it's safer to over-count.
        """
        pass 
