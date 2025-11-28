import uuid
from typing import List, Optional, Dict
from sqlalchemy.orm import Session
from database import SessionLocal
from sql_models import Feed
from models import FeedConfig, FilterConfig

class FeedConfigManager:
    """Manages feed configurations (persistence via SQL)."""
    
    def __init__(self, config_file=None):
        # config_file arg kept for compatibility but unused
        pass
    
    def get_db(self):
        return SessionLocal()

    def _to_pydantic(self, feed_model: Feed) -> FeedConfig:
        """Convert SQL model to Pydantic model."""
        filters = None
        if feed_model.filters:
            filters = FilterConfig(**feed_model.filters)
        
        source_filters = {}
        if feed_model.source_filters:
            for k, v in feed_model.source_filters.items():
                source_filters[int(k)] = FilterConfig(**v)

        return FeedConfig(
            id=feed_model.id,
            name=feed_model.name,
            source_channel_ids=feed_model.source_channel_ids,
            destination_channel_id=feed_model.destination_channel_id,
            active=feed_model.active,
            delay_enabled=feed_model.delay_enabled,
            filters=filters,
            source_filters=source_filters,
            error=feed_model.error
        )

    def get_user_feeds(self, user_id: str) -> List[FeedConfig]:
        """Get all feeds for a user."""
        db = self.get_db()
        try:
            feeds = db.query(Feed).filter(Feed.user_id == user_id).all()
            return [self._to_pydantic(f) for f in feeds]
        finally:
            db.close()
    
    def get_feed(self, user_id: str, feed_id: str) -> Optional[FeedConfig]:
        """Get a specific feed."""
        db = self.get_db()
        try:
            feed = db.query(Feed).filter(Feed.user_id == user_id, Feed.id == feed_id).first()
            return self._to_pydantic(feed) if feed else None
        finally:
            db.close()
    
    def create_feed(self, user_id: str, feed: FeedConfig) -> FeedConfig:
        """Create a new feed."""
        db = self.get_db()
        try:
            if not feed.id:
                feed.id = str(uuid.uuid4())
            
            # Convert Pydantic models to dicts for JSON columns
            filters_dict = feed.filters.model_dump() if feed.filters else None
            source_filters_dict = {str(k): v.model_dump() for k, v in feed.source_filters.items()} if feed.source_filters else None

            db_feed = Feed(
                id=feed.id,
                user_id=user_id,
                name=feed.name,
                source_channel_ids=feed.source_channel_ids,
                destination_channel_id=feed.destination_channel_id,
                active=feed.active,
                delay_enabled=feed.delay_enabled,
                filters=filters_dict,
                source_filters=source_filters_dict,
                error=feed.error
            )
            db.add(db_feed)
            db.commit()
            db.refresh(db_feed)
            return self._to_pydantic(db_feed)
        finally:
            db.close()
    
    def update_feed(self, user_id: str, feed_id: str, updates: Dict) -> Optional[FeedConfig]:
        """Update a feed."""
        db = self.get_db()
        try:
            feed = db.query(Feed).filter(Feed.user_id == user_id, Feed.id == feed_id).first()
            if not feed:
                return None
            
            for key, value in updates.items():
                if hasattr(feed, key):
                    # Handle special JSON fields
                    # Handle special JSON fields
                    if key == 'filters':
                         # If value is dict, use it. If Pydantic, dump it. If None, set to None.
                         if value is None:
                             setattr(feed, key, None)
                         else:
                             setattr(feed, key, value.model_dump() if hasattr(value, 'model_dump') else value)
                    elif key == 'source_filters':
                         if value is None:
                             setattr(feed, key, None)
                         else:
                             # Convert keys to strings for JSON storage if needed
                             val_to_store = {}
                             for k, v in value.items():
                                 val_to_store[str(k)] = v.model_dump() if hasattr(v, 'model_dump') else v
                             setattr(feed, key, val_to_store)
                    else:
                        setattr(feed, key, value)
            
            db.commit()
            db.refresh(feed)
            return self._to_pydantic(feed)
        finally:
            db.close()
    
    def delete_feed(self, user_id: str, feed_id: str) -> bool:
        """Delete a feed."""
        db = self.get_db()
        try:
            feed = db.query(Feed).filter(Feed.user_id == user_id, Feed.id == feed_id).first()
            if not feed:
                return False
            
            db.delete(feed)
            db.commit()
            return True
        finally:
            db.close()
    
    def get_all_active_feeds(self) -> List[tuple[str, FeedConfig]]:
        """Get all active feeds across all users (for background worker)."""
        db = self.get_db()
        try:
            feeds = db.query(Feed).filter(Feed.active == True).all()
            return [(f.user_id, self._to_pydantic(f)) for f in feeds]
        finally:
            db.close()
