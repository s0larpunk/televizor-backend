import json
import os
from datetime import datetime, timedelta
from typing import Dict, Optional
import logging
from models import SubscriptionStatus, SubscriptionTier

logger = logging.getLogger(__name__)

class UserManager:
    def __init__(self, config_file: str = "users_config.json"):
        self.config_file = config_file
        self.users: Dict[str, dict] = {}
        self._last_mtime = 0
        self._load_config()

    def _load_config(self):
        """Load user configuration from JSON file."""
        if os.path.exists(self.config_file):
            try:
                mtime = os.path.getmtime(self.config_file)
                self._last_mtime = mtime
                with open(self.config_file, 'r') as f:
                    self.users = json.load(f)
            except json.JSONDecodeError:
                logger.error(f"Error decoding {self.config_file}, starting with empty config")
                self.users = {}
        else:
            self.users = {}

    def _check_reload(self):
        """Check if config file has changed and reload if necessary."""
        if os.path.exists(self.config_file):
            mtime = os.path.getmtime(self.config_file)
            if mtime > self._last_mtime:
                logger.info(f"Config file {self.config_file} changed, reloading...")
                self._load_config()

    def _save_config(self):
        """Save user configuration to JSON file."""
        try:
            with open(self.config_file, 'w') as f:
                json.dump(self.users, f, indent=4)
            # Update mtime after save to prevent unnecessary reload
            self._last_mtime = os.path.getmtime(self.config_file)
        except Exception as e:
            logger.error(f"Error saving user config: {e}")

    def get_subscription_status(self, phone: str) -> SubscriptionStatus:
        """Get subscription status for a user."""
        # Check for external updates
        self._check_reload()
        
        # Initialize new user with free tier (no auto-trial)
        if phone not in self.users:
            self.users[phone] = {
                "tier": SubscriptionTier.FREE,
                "trial_start_date": None,
                "expiry_date": None
            }
            self._save_config()
        
        user_data = self.users[phone]
        tier = user_data.get("tier", SubscriptionTier.FREE)
        trial_start = user_data.get("trial_start_date")
        expiry = user_data.get("expiry_date")
        
        is_expired = False
        trial_available = trial_start is None  # Trial available if never activated
        
        # Check if trial/premium has expired
        if expiry and tier in [SubscriptionTier.TRIAL, SubscriptionTier.PREMIUM]:
            expiry_date = datetime.fromisoformat(expiry)
            if datetime.now() > expiry_date:
                is_expired = True
                # Note: Auto-downgrade is handled in main.py where feed_config_manager is available
        
        return SubscriptionStatus(
            tier=tier,
            trial_start_date=trial_start,
            expiry_date=expiry,
            is_expired=is_expired,
            trial_available=trial_available
        )

    def start_trial(self, phone: str):
        """Initialize a 3-day trial for a user."""
        # Check if trial was already used
        if phone in self.users and self.users[phone].get("trial_start_date"):
            raise Exception("Trial already activated for this account")
        
        self.users[phone] = {
            "tier": SubscriptionTier.TRIAL,
            "trial_start_date": datetime.now().isoformat(),
            "expiry_date": (datetime.now() + timedelta(days=3)).isoformat(),
            "telegram_id": self.users.get(phone, {}).get("telegram_id")  # Preserve if exists
        }
        self._save_config()
        logger.info(f"Started trial for user {phone}")

    def link_telegram_id(self, phone: str, telegram_id: int):
        """Link a Telegram ID to a user's phone number."""
        if phone in self.users:
            self.users[phone]["telegram_id"] = telegram_id
            self._save_config()
            logger.info(f"Linked Telegram ID {telegram_id} to user {phone}")

    def get_phone_by_telegram_id(self, telegram_id: int) -> Optional[str]:
        """Find phone number associated with a Telegram ID."""
        for phone, data in self.users.items():
            if data.get("telegram_id") == telegram_id:
                return phone
        return None

    def upgrade_to_premium(self, phone: str):
        """Upgrade user to premium."""
        current_data = self.users.get(phone, {})
        
        # Calculate expiry date (30 days from now)
        expiry_date = (datetime.now() + timedelta(days=30)).isoformat()
        
        self.users[phone] = {
            "tier": SubscriptionTier.PREMIUM,
            "trial_start_date": current_data.get("trial_start_date"),
            "expiry_date": expiry_date,
            "telegram_id": current_data.get("telegram_id")  # Preserve Telegram ID
        }
        self._save_config()
        logger.info(f"Upgraded user {phone} to premium until {expiry_date}")

    def downgrade_to_free(self, phone: str, feed_config_manager=None):
        """Downgrade user to free tier and handle feed restrictions."""
        current_data = self.users.get(phone, {})
        self.users[phone] = {
            "tier": SubscriptionTier.FREE,
            "trial_start_date": current_data.get("trial_start_date"),
            "expiry_date": None,
            "telegram_id": current_data.get("telegram_id")  # Preserve Telegram ID
        }
        self._save_config()
        logger.info(f"Downgraded user {phone} to free")
        
        # Handle feed restrictions if feed_config_manager is provided
        if feed_config_manager:
            try:
                feeds = feed_config_manager.get_user_feeds(phone)
                if not feeds:
                    return
                
                # Helper function to check if feed has filters
                def has_filters(feed):
                    if feed.filters:
                        f = feed.filters
                        if (f.keywords_include or f.keywords_exclude or 
                            f.has_image is not None or f.has_video is not None or 
                            f.max_messages_per_hour is not None or f.max_messages_per_day is not None):
                            return True
                    if feed.source_filters and len(feed.source_filters) > 0:
                        return True
                    return False
                
                # Deactivate all feeds with filters
                for feed in feeds:
                    if feed.active and has_filters(feed):
                        feed_config_manager.update_feed(phone, feed.id, {
                            "active": False,
                            "error": "INACTIVE - Upgrade to Premium to use filters"
                        })
                        logger.info(f"Deactivated feed {feed.id} with filters for user {phone}")
                
                # Ensure only first feed (without filters) is active
                feeds = feed_config_manager.get_user_feeds(phone)  # Reload after updates
                active_count = sum(1 for f in feeds if f.active)
                
                if active_count == 0:
                    # Activate first feed without filters
                    for feed in feeds:
                        if not has_filters(feed):
                            feed_config_manager.update_feed(phone, feed.id, {
                                "active": True,
                                "error": None
                            })
                            logger.info(f"Activated first feed {feed.id} without filters for user {phone}")
                            break
                    else:
                        # All feeds have filters, set error on first feed
                        if feeds:
                            feed_config_manager.update_feed(phone, feeds[0].id, {
                                "active": False,
                                "error": "INACTIVE - Remove filters or upgrade to Premium"
                            })
                            logger.info(f"All feeds have filters for user {phone}, marked first as inactive")
                
                elif active_count > 1:
                    # Deactivate all but the first active feed
                    first_active_found = False
                    for feed in feeds:
                        if feed.active:
                            if not first_active_found:
                                first_active_found = True
                            else:
                                feed_config_manager.update_feed(phone, feed.id, {"active": False})
                                logger.info(f"Deactivated extra feed {feed.id} for free user {phone}")
                
            except Exception as e:
                logger.error(f"Error handling feeds during downgrade for {phone}: {e}")
