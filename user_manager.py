import logging
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy.orm import Session
from database import SessionLocal
from sql_models import User
from models import SubscriptionStatus, SubscriptionTier

logger = logging.getLogger(__name__)

class UserManager:
    def __init__(self):
        pass

    def get_db(self):
        return SessionLocal()

    def get_subscription_status(self, phone: str) -> SubscriptionStatus:
        """Get subscription status for a user."""
        db = self.get_db()
        try:
            user = db.query(User).filter(User.phone == phone).first()
            
            if not user:
                # Create new user
                user = User(phone=phone, tier=SubscriptionTier.FREE)
                db.add(user)
                db.commit()
                db.refresh(user)
            
            is_expired = False
            trial_available = user.trial_start_date is None
            
            if user.expiry_date and user.tier in [SubscriptionTier.TRIAL, SubscriptionTier.PREMIUM]:
                if datetime.utcnow() > user.expiry_date:
                    is_expired = True
            
            return SubscriptionStatus(
                tier=user.tier,
                trial_start_date=user.trial_start_date.isoformat() if user.trial_start_date else None,
                expiry_date=user.expiry_date.isoformat() if user.expiry_date else None,
                is_expired=is_expired,
                trial_available=trial_available
            )
        finally:
            db.close()

    def start_trial(self, phone: str):
        """Initialize a 3-day trial for a user."""
        db = self.get_db()
        try:
            user = db.query(User).filter(User.phone == phone).first()
            if not user:
                raise Exception("User not found")
            
            if user.trial_start_date:
                raise Exception("Trial already activated for this account")
            
            user.tier = SubscriptionTier.TRIAL
            user.trial_start_date = datetime.utcnow()
            user.expiry_date = datetime.utcnow() + timedelta(days=3)
            
            db.commit()
            logger.info(f"Started trial for user {phone}")
        finally:
            db.close()

    def link_telegram_id(self, phone: str, telegram_id: int):
        """Link a Telegram ID to a user's phone number."""
        db = self.get_db()
        try:
            user = db.query(User).filter(User.phone == phone).first()
            if user:
                user.telegram_id = telegram_id
                db.commit()
                logger.info(f"Linked Telegram ID {telegram_id} to user {phone}")
        finally:
            db.close()

    def get_phone_by_telegram_id(self, telegram_id: int) -> Optional[str]:
        """Find phone number associated with a Telegram ID."""
        db = self.get_db()
        try:
            user = db.query(User).filter(User.telegram_id == telegram_id).first()
            return user.phone if user else None
        finally:
            db.close()

    def upgrade_to_premium(self, phone: str):
        """Upgrade user to premium."""
        db = self.get_db()
        try:
            user = db.query(User).filter(User.phone == phone).first()
            if user:
                user.tier = SubscriptionTier.PREMIUM
                user.expiry_date = datetime.utcnow() + timedelta(days=30)
                db.commit()
                logger.info(f"Upgraded user {phone} to premium")
        finally:
            db.close()

    def downgrade_to_free(self, phone: str, feed_config_manager=None):
        """Downgrade user to free tier and handle feed restrictions."""
        db = self.get_db()
        try:
            user = db.query(User).filter(User.phone == phone).first()
            if user:
                user.tier = SubscriptionTier.FREE
                user.expiry_date = None
                db.commit()
                logger.info(f"Downgraded user {phone} to free")
                
                # Handle feed restrictions
                if feed_config_manager:
                    # We can pass the same db session if we want, but for now let's let manager handle it
                    # Or better, we should probably do this logic here since we have the DB
                    pass 
                    # Note: The original logic called feed_config_manager. 
                    # We will rely on the caller to handle feed restrictions or implement it here.
                    # For now, I'll replicate the logic using feed_config_manager which will be updated to use DB.
                    
                    try:
                        feeds = feed_config_manager.get_user_feeds(phone)
                        if not feeds:
                            return
                        
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
                        
                        # Reload feeds
                        feeds = feed_config_manager.get_user_feeds(phone)
                        active_count = sum(1 for f in feeds if f.active)
                        
                        if active_count == 0:
                            for feed in feeds:
                                if not has_filters(feed):
                                    feed_config_manager.update_feed(phone, feed.id, {
                                        "active": True,
                                        "error": None
                                    })
                                    break
                            else:
                                if feeds:
                                    feed_config_manager.update_feed(phone, feeds[0].id, {
                                        "active": False,
                                        "error": "INACTIVE - Remove filters or upgrade to Premium"
                                    })
                        elif active_count > 1:
                            first_active_found = False
                            for feed in feeds:
                                if feed.active:
                                    if not first_active_found:
                                        first_active_found = True
                                    else:
                                        feed_config_manager.update_feed(phone, feed.id, {"active": False})

                    except Exception as e:
                        logger.error(f"Error handling feeds during downgrade for {phone}: {e}")

        finally:
            db.close()

