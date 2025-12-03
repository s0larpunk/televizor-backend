import asyncio
import logging
from typing import Dict, Set
from datetime import datetime, timedelta
from telethon import TelegramClient, events, utils
from telethon.errors import SessionRevokedError, AuthKeyError
from telegram_client import get_telegram_manager
from feed_manager import FeedConfigManager
from user_manager import UserManager
from models import SubscriptionTier
from sql_models import MessageLog
from database import SessionLocal
from redis_client import RateLimiter
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

    def _log_db(self, user_phone: str, source_id: int, dest_id: int, msg_id: int, status: str, details: str = None):
        """Log message event to database."""
        try:
            db = SessionLocal()
            log_entry = MessageLog(
                user_phone=user_phone,
                source_channel_id=source_id,
                destination_channel_id=dest_id,
                message_id=msg_id,
                status=status,
                details=details
            )
            db.add(log_entry)
            db.commit()
            db.close()
        except Exception as e:
            logger.error(f"Failed to write to message log: {e}")

    
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

    async def _wait_and_flush(self, key, callback):
        """Wait for a short duration and then flush the album."""
        try:
            await asyncio.sleep(2.0)
            await callback(key)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in album flush timer: {e}")

    async def _setup_user_handlers(self, user_id: str, feeds: list, sub_status):
        """Set up message handlers for a user's feeds."""
        try:
            # Use Telegram ID if available, otherwise fallback to phone (user_id here is phone)
            user_identifier = str(sub_status.telegram_id) if sub_status.telegram_id else user_id
            
            # Load session string from DB
            # user_id is the phone number here
            session_string = self.user_manager.get_session(user_id)
            
            manager = get_telegram_manager(user_identifier, session_string)
            
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
            
            # Album buffering: (source_id, grouped_id) -> { 'message_ids': set, 'timer': Task, 'feeds': list }
            pending_albums = {}

            async def flush_album(key):
                if key not in pending_albums:
                    return
                
                data = pending_albums.pop(key)
                source_id = key[0]
                message_ids = sorted(list(data['message_ids']))
                feeds_to_process = data['feeds']
                
                logger.info(f"Flushing album {key[1]} with {len(message_ids)} messages from {source_id}")
                
                for feed in feeds_to_process:
                    try:
                        # We use the first message for filter checks in the album?
                        # Or we should have filtered them individually before adding?
                        # If we filter individually, we might drop some parts of the album.
                        # But `handler` logic below does filtering BEFORE buffering.
                        # So `message_ids` here are already filtered/approved.
                        
                        delay = 15 if getattr(feed, 'delay_enabled', True) else 0
                        asyncio.create_task(
                            self._forward_message(
                                client=client,
                                source_chat_id=source_id,
                                destination_channel_id=feed.destination_channel_id,
                                message_id=message_ids, # Pass list
                                delay_seconds=delay
                            )
                        )
                    except Exception as e:
                        logger.error(f"Error flushing album to feed {feed.id}: {e}")

            # Register handler for new messages
            # We'll listen to ALL messages and filter inside to debug ID mismatches
            @client.on(events.NewMessage())
            async def handler(event):
                try:
                    # Debug logging for ALL messages
                    chat_id = event.chat_id
                    sender_id = event.sender_id
                    
                    # Try to get the peer ID in different formats
                    peer_id = utils.get_peer_id(event.message.peer_id)
                    normalized_id = utils.get_peer_id(event.message.peer_id, add_mark=False)
                    
                    # logger.info(f"DEBUG: Event received. ChatID: {chat_id}, PeerID: {peer_id}, Normalized: {normalized_id}")
                    
                    if normalized_id not in source_to_feeds:
                        return

                    source_channel_id = normalized_id
                    relevant_feeds = source_to_feeds.get(source_channel_id, [])
                    
                    if not relevant_feeds:
                        return
                    
                    # Log reception (difference caught)
                    self._log_db(
                        user_phone=user_id,
                        source_id=source_channel_id,
                        dest_id=0, # Not assigned yet
                        msg_id=event.message.id,
                        status='received',
                        details=f"Received update from {source_channel_id} for user {user_id}"
                    )
                    
                    # Check if this is part of an album
                    grouped_id = event.message.grouped_id
                    
                    # Filter feeds first
                    valid_feeds = []
                    for feed in relevant_feeds:
                        # 1. Check Source Filters & Limits
                        source_filter = feed.source_filters.get(source_channel_id) if feed.source_filters else None
                        if source_filter:
                            if not is_advanced:
                                continue
                            if not self._check_filters(event.message, source_filter):
                                continue
                            if not self._check_rate_limit(user_id, f"source_{source_channel_id}", source_filter):
                                continue

                        # 2. Check Global Filters & Limits
                        if feed.filters:
                            if not is_advanced:
                                continue
                            if not self._check_filters(event.message, feed.filters):
                                continue
                            if not self._check_rate_limit(user_id, f"feed_{feed.id}", feed.filters):
                                continue
                        
                        valid_feeds.append(feed)
                    
                    if not valid_feeds:
                        return

                    if grouped_id:
                        key = (source_channel_id, grouped_id)
                        if key not in pending_albums:
                            pending_albums[key] = {
                                'message_ids': set(),
                                'feeds': valid_feeds, # Assuming feeds are same for all msgs in album
                                'timer': None
                            }
                        
                        pending_albums[key]['message_ids'].add(event.message.id)
                        
                        # Debounce
                        if pending_albums[key]['timer']:
                            pending_albums[key]['timer'].cancel()
                        
                        # Wait 2 seconds for more messages
                        pending_albums[key]['timer'] = asyncio.create_task(
                            self._wait_and_flush(key, flush_album)
                        )
                    else:
                        # Single message
                        for feed in valid_feeds:
                            try:
                                logger.info(f"Processing feed {feed.id} for message {event.message.id}")
                                delay = 15 if getattr(feed, 'delay_enabled', True) else 0
                                asyncio.create_task(
                                    self._forward_message(
                                        client=client,
                                        source_chat_id=source_channel_id,
                                        destination_channel_id=feed.destination_channel_id,
                                        message_id=event.message.id,
                                        delay_seconds=delay,
                                        user_phone=user_id # Pass user_id for logging
                                    )
                                )
                                logger.info(f"Queued message {event.message.id} for forwarding to {feed.destination_channel_id}")
                                self._log_db(user_id, source_channel_id, feed.destination_channel_id, event.message.id, 'queued')
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
            
        except (SessionRevokedError, AuthKeyError) as e:
            logger.error(f"Session revoked for user {user_id}: {e}")
            # Stop processing for this user
            if user_id in self.active_handlers:
                self.active_handlers[user_id].clear()
                del self.active_handlers[user_id]
            # We could cleanup client here, but let's leave it to the main app or next retry
            # Actually, if we don't cleanup, it might keep trying with bad session?
            # But `_setup_user_handlers` is called when config changes.
            # If session is bad, we should probably stop trying until re-login.
            # For now, just logging is enough to prevent crash loop if we were crashing.
            # But we were catching Exception, so it wasn't crashing.
            # This specific catch just makes it explicit.
        except Exception as e:
            logger.error(f"Failed to setup handlers for user {user_id}: {e}")


    async def _forward_message(self, client, source_chat_id: int, destination_channel_id: int, message_id: any, delay_seconds: int, user_phone: str = None):
        """
        Forward message using the existing client connection.
        """
        try:
            # Calculate schedule time if delay is requested
            schedule_date = None
            if delay_seconds > 0:
                schedule_date = datetime.utcnow() + timedelta(seconds=delay_seconds)
            
            # Resolve entity first to ensure we have the access hash
            try:
                destination_entity = await client.get_input_entity(destination_channel_id)
            except ValueError:
                # If it's a user ID, maybe we need to force fetch or it's 'me'
                logger.warning(f"Could not resolve entity {destination_channel_id} from cache. Attempting to fetch...")
                try:
                    destination_entity = await client.get_entity(destination_channel_id)
                except Exception as fetch_err:
                    logger.error(f"Failed to fetch entity {destination_channel_id}: {fetch_err}")
                    raise fetch_err

            await client.forward_messages(
                entity=destination_entity,
                messages=message_id,
                from_peer=source_chat_id,
                schedule=schedule_date
            )
            
            msg_desc = f"messages {message_id}" if isinstance(message_id, list) else f"message {message_id}"
            
            if schedule_date:
                logger.info(f"Successfully scheduled {msg_desc} from {source_chat_id} to {destination_channel_id} for {schedule_date}")
                if user_phone:
                    self._log_db(user_phone, source_chat_id, destination_channel_id, message_id if isinstance(message_id, int) else 0, 'scheduled', str(schedule_date))
            else:
                logger.info(f"Successfully forwarded {msg_desc} from {source_chat_id} to {destination_channel_id}")
                if user_phone:
                    self._log_db(user_phone, source_chat_id, destination_channel_id, message_id if isinstance(message_id, int) else 0, 'forwarded')
            
        except Exception as e:
            logger.error(f"Error forwarding message {message_id} to {destination_channel_id}: {e}")


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
