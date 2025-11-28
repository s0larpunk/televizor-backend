import json
from pathlib import Path
from typing import List, Dict, Optional
import uuid
from models import FeedConfig

class FeedConfigManager:
    """Manages feed configurations (persistence)."""
    
    def __init__(self, config_file: Path):
        self.config_file = config_file
        self._ensure_file()
    
    def _ensure_file(self):
        """Ensure the config file exists."""
        if not self.config_file.exists():
            self.config_file.write_text(json.dumps({}))
    
    def _load(self) -> Dict[str, Dict]:
        """Load all feed configs."""
        try:
            return json.loads(self.config_file.read_text())
        except:
            return {}
    
    def _save(self, data: Dict[str, Dict]):
        """Save feed configs."""
        self.config_file.write_text(json.dumps(data, indent=2))
    
    def get_user_feeds(self, user_id: str) -> List[FeedConfig]:
        """Get all feeds for a user."""
        data = self._load()
        user_feeds = data.get(user_id, {})
        return [FeedConfig(**feed) for feed in user_feeds.values()]
    
    def get_feed(self, user_id: str, feed_id: str) -> Optional[FeedConfig]:
        """Get a specific feed."""
        data = self._load()
        feed_data = data.get(user_id, {}).get(feed_id)
        return FeedConfig(**feed_data) if feed_data else None
    
    def create_feed(self, user_id: str, feed: FeedConfig) -> FeedConfig:
        """Create a new feed."""
        data = self._load()
        
        # Generate ID if not provided
        if not feed.id:
            feed.id = str(uuid.uuid4())
        
        # Ensure user key exists
        if user_id not in data:
            data[user_id] = {}
        
        # Save feed
        data[user_id][feed.id] = feed.model_dump()
        self._save(data)
        
        return feed
    
    def update_feed(self, user_id: str, feed_id: str, updates: Dict) -> Optional[FeedConfig]:
        """Update a feed."""
        data = self._load()
        
        if user_id not in data or feed_id not in data[user_id]:
            return None
        
        # Apply updates
        data[user_id][feed_id].update(updates)
        self._save(data)
        
        return FeedConfig(**data[user_id][feed_id])
    
    def delete_feed(self, user_id: str, feed_id: str) -> bool:
        """Delete a feed."""
        data = self._load()
        
        if user_id not in data or feed_id not in data[user_id]:
            return False
        
        del data[user_id][feed_id]
        self._save(data)
        
        return True
    
    def get_all_active_feeds(self) -> List[tuple[str, FeedConfig]]:
        """Get all active feeds across all users (for background worker)."""
        data = self._load()
        active_feeds = []
        
        for user_id, feeds in data.items():
            for feed_id, feed_data in feeds.items():
                feed = FeedConfig(**feed_data)
                if feed.active:
                    active_feeds.append((user_id, feed))
        
        return active_feeds
