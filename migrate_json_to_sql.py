import json
import os
from datetime import datetime
from database import SessionLocal, engine, Base
from sql_models import User, Feed
from models import SubscriptionTier

# Create tables
Base.metadata.create_all(bind=engine)

def migrate_users(db):
    if not os.path.exists("users_config.json"):
        print("No users_config.json found.")
        return

    with open("users_config.json", "r") as f:
        users_data = json.load(f)

    for phone, data in users_data.items():
        # Check if user exists
        existing_user = db.query(User).filter(User.phone == phone).first()
        if existing_user:
            print(f"User {phone} already exists, skipping.")
            continue

        trial_start = None
        if data.get("trial_start_date"):
            try:
                trial_start = datetime.fromisoformat(data["trial_start_date"])
            except:
                pass
        
        expiry = None
        if data.get("expiry_date"):
            try:
                expiry = datetime.fromisoformat(data["expiry_date"])
            except:
                pass

        user = User(
            phone=phone,
            telegram_id=data.get("telegram_id"),
            tier=data.get("tier", "free"),
            trial_start_date=trial_start,
            expiry_date=expiry
        )
        db.add(user)
    
    db.commit()
    print(f"Migrated {len(users_data)} users.")

def migrate_feeds(db):
    if not os.path.exists("feeds_config.json"):
        print("No feeds_config.json found.")
        return

    with open("feeds_config.json", "r") as f:
        feeds_data = json.load(f)

    count = 0
    for user_id, user_feeds in feeds_data.items():
        # Ensure user exists (might be in feeds but not users config if legacy)
        user = db.query(User).filter(User.phone == user_id).first()
        if not user:
            print(f"User {user_id} not found for feeds, creating default free user.")
            user = User(phone=user_id, tier="free")
            db.add(user)
            db.commit()

        for feed_id, feed_data in user_feeds.items():
            existing_feed = db.query(Feed).filter(Feed.id == feed_id).first()
            if existing_feed:
                continue

            feed = Feed(
                id=feed_id,
                user_id=user_id,
                name=feed_data.get("name"),
                source_channel_ids=feed_data.get("source_channel_ids", []),
                destination_channel_id=feed_data.get("destination_channel_id"),
                active=feed_data.get("active", True),
                delay_enabled=feed_data.get("delay_enabled", True),
                filters=feed_data.get("filters"),
                source_filters=feed_data.get("source_filters"),
                error=feed_data.get("error")
            )
            db.add(feed)
            count += 1
    
    db.commit()
    print(f"Migrated {count} feeds.")

if __name__ == "__main__":
    db = SessionLocal()
    try:
        migrate_users(db)
        migrate_feeds(db)
        print("Migration complete.")
    except Exception as e:
        print(f"Migration failed: {e}")
    finally:
        db.close()
