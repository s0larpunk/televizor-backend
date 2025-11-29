import asyncio
import logging
from typing import Dict, Set
from datetime import datetime, timedelta
from telethon import TelegramClient, events, utils
from telegram_client import get_telegram_manager
from feed_manager import FeedConfigManager
from user_manager import UserManager
from models import SubscriptionTier
from redis_client import RateLimiter
from tasks import forward_message_task
import config

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class FeedWorker:
    """Background worker that listens to channels and queues messages for forwarding."""
    
    def __init__(self, feed_config_manager: FeedConfigManager):
        self.feed_config_manager = feed_config_manager
        self.user_manager = UserManager()
        self.active_handlers: Dict[str, Set] = {}  # user_id -> set of handler references
        self.user_config_hashes: Dict[str, str] = {} # user_id -> config hash
        self.running = False
    
    async def start(self):
        """Start the feed worker."""
        self.running = True
        logger.info("Feed worker started")
        
        while self.running:
            try:
                await self._sync_feeds()
                await asyncio.sleep(10)  # Check for config changes every 10 seconds
            except Exception as e:
                logger.error(f"Error in feed worker: {e}")
                await asyncio.sleep(5)
    
    async def stop(self):
        """Stop the feed worker."""
        self.running = False
        logger.info("Feed worker stopped")
    
    async def _sync_feeds(self):
        """Sync active feeds with current configuration."""
        active_feeds = self.feed_config_manager.get_all_active_feeds()
        
        # Group feeds by user
        feeds_by_user: Dict[str, list] = {}
        for user_id, feed in active_feeds:
            # logger.info(f"Syncing feed for user: {repr(user_id)}")
            if user_id not in feeds_by_user:
                feeds_by_user[user_id] = []
            feeds_by_user[user_id].append(feed)
        
        # Set up handlers for each user
        for user_id, feeds in feeds_by_user.items():
            # Compute config hash to detect changes
            sorted_feeds = sorted(feeds, key=lambda f: f.id or "")
            current_hash = str([f.model_dump() for f in sorted_feeds])
            
            # Also check subscription status as part of state
            sub_status = self.user_manager.get_subscription_status(user_id)
            current_hash += f"_{sub_status.tier}_{sub_status.is_expired}"
            
            if user_id not in self.active_handlers or self.user_config_hashes.get(user_id) != current_hash:
                logger.info(f"Config changed for user {user_id}, refreshing handlers")
                await self._setup_user_handlers(user_id, feeds, sub_status)
                self.user_config_hashes[user_id] = current_hash
    
    def _check_filters(self, message, filters) -> bool:
        """Check if message passes filters. Returns True if allowed."""
        if not filters:
            return True
            
        text = message.text or ""
        
        # Keyword inclusion
        if filters.keywords_include:
            if not any(k.lower() in text.lower() for k in filters.keywords_include):
                return False
                
        # Keyword exclusion
        if filters.keywords_exclude:
            if any(k.lower() in text.lower() for k in filters.keywords_exclude):
                return False
        
        # Media checks
        if filters.has_image is not None:
            has_photo = bool(message.photo)
            if filters.has_image != has_photo:
                return False
                
        if filters.has_video is not None:
            has_video = bool(message.video or (message.document and message.document.mime_type.startswith('video/')))
            if filters.has_video != has_video:
                return False
                
        return True

    def _check_rate_limit(self, user_id: str, key: str, filters) -> bool:
        """Check rate limits using Redis. Returns True if allowed."""
        if not filters:
            return True
            
        return RateLimiter.check_rate_limit(
            user_id, 
            key, 
            max_hourly=filters.max_messages_per_hour, 
            max_daily=filters.max_messages_per_day
        )

    async def _setup_user_handlers(self, user_id: str, feeds: list, sub_status):
        """Set up message handlers for a user's feeds."""
        try:
            # Use Telegram ID if available, otherwise fallback to phone (user_id here is phone)
            user_identifier = str(sub_status.telegram_id) if sub_status.telegram_id else user_id
            manager = get_telegram_manager(user_identifier)
            
            # Check if authenticated
            if not await manager.is_authenticated():
                logger.warning(f"User {user_id} not authenticated, skipping")
                return
            
            client = await manager.initialize()
            
            if not client.is_connected():
                await client.connect()
            
            # Remove ALL existing handlers
            try:
                for callback, event in client.list_event_handlers():
                    client.remove_event_handler(callback)
            except Exception as e:
                logger.warning(f"Error clearing handlers: {e}")
            
            if user_id in self.active_handlers:
                self.active_handlers[user_id].clear()
            
            # Handle Trial Expiry
            is_advanced = sub_status.tier in [SubscriptionTier.PREMIUM_ADVANCED, SubscriptionTier.PREMIUM, SubscriptionTier.TRIAL]
            
            if sub_status.is_expired:
                # Find the one allowed feed (no filters)
                allowed_feed = None
                for feed in feeds:
                    if not feed.filters and not feed.source_filters: # Simple check for now
                        allowed_feed = feed
                        break
                
                if allowed_feed:
                    feeds = [allowed_feed]
                    logger.info(f"Trial expired for {user_id}. Only allowing feed {allowed_feed.id}")
                else:
                    feeds = []
                    logger.info(f"Trial expired for {user_id}. No allowed feeds found.")
                    return

            # Map source -> list of feeds
            source_to_feeds: Dict[int, list] = {}
            for feed in feeds:
                for source_id in feed.source_channel_ids:
                    if source_id not in source_to_feeds:
                        source_to_feeds[source_id] = []
                    source_to_feeds[source_id].append(feed)
            
            # Register handler for new messages
            @client.on(events.NewMessage(chats=list(source_to_feeds.keys())))
            async def handler(event):
                try:
                    source_channel_id = utils.get_peer_id(event.message.peer_id, add_mark=False)
                    logger.info(f"Received message from {event.chat_id} (normalized: {source_channel_id})")
                    
                    relevant_feeds = source_to_feeds.get(source_channel_id, [])
                    logger.info(f"Found {len(relevant_feeds)} relevant feeds for source {source_channel_id}")
                    
                    if not relevant_feeds:
                        logger.warning(f"No feeds found for source {source_channel_id}. Available sources: {list(source_to_feeds.keys())}")
                        return
                    
                    for feed in relevant_feeds:
                        try:
                            logger.info(f"Processing feed {feed.id} for message {event.message.id}")
                            # 1. Check Source Filters & Limits
                            source_filter = feed.source_filters.get(source_channel_id) if feed.source_filters else None
                            if source_filter:
                                if not is_advanced:
                                    # Skip filtering for basic users (or block if filter exists? 
                                    # Logic in main.py prevents activating feeds with filters for non-advanced.
                                    # But if they downgrade, we should probably ignore filters or block feed.
                                    # Let's block the feed if it has filters and user is not advanced.
                                    logger.info(f"Skipping feed {feed.id} - Filters require Advanced Premium")
                                    continue
                                    
                                if not self._check_filters(event.message, source_filter):
                                    logger.info(f"Filtered by source filter: {source_filter}")
                                    continue
                                if not self._check_rate_limit(user_id, f"source_{source_channel_id}", source_filter):
                                    logger.info(f"Rate limit reached for feed {feed.id} source {source_channel_id}")
                                    continue

                            # 2. Check Global Filters & Limits
                            if feed.filters:
                                if not is_advanced:
                                    logger.info(f"Skipping feed {feed.id} - Filters require Advanced Premium")
                                    continue

                                if not self._check_filters(event.message, feed.filters):
                                    logger.info(f"Filtered by global filter: {feed.filters}")
                                    continue
                                if not self._check_rate_limit(user_id, f"feed_{feed.id}", feed.filters):
                                    logger.info(f"Global rate limit reached for feed {feed.id}")
                                    continue

                            # 3. Queue for Forwarding
                            delay = 15 if getattr(feed, 'delay_enabled', True) else 0
                            
                            # Push to Celery Queue
                            forward_message_task.delay(
                                user_id=user_id,
                                source_chat_id=source_channel_id,
                                destination_channel_id=feed.destination_channel_id,
                                message_id=event.message.id,
                                delay_seconds=delay
                            )
                            
                            logger.info(f"Queued message {event.message.id} for forwarding to {feed.destination_channel_id}")
                            
                        except Exception as e:
                            logger.error(f"Error processing feed {feed.id}: {e}")
                            
                except Exception as e:
                    logger.error(f"Error in message handler: {e}")
            
            # Store handler reference
            if user_id not in self.active_handlers:
                self.active_handlers[user_id] = set()
            self.active_handlers[user_id].add(handler)
            
            # Start the client if not already running
            if not client.is_connected():
                await client.start()
            
            logger.info(f"Set up handlers for user {user_id} with {len(feeds)} feeds")
            
        except Exception as e:
            logger.error(f"Failed to setup handlers for user {user_id}: {e}")


# Global worker instance
_worker: FeedWorker = None

async def start_feed_worker():
    """Start the global feed worker."""
    global _worker
    if _worker is None:
        feed_config_manager = FeedConfigManager()
        _worker = FeedWorker(feed_config_manager)
    
    await _worker.start()

async def stop_feed_worker():
    """Stop the global feed worker."""
    global _worker
    if _worker:
        await _worker.stop()
