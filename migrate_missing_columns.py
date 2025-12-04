from database import engine
from sqlalchemy import text, inspect
import logging
import sys

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def run_migration():
    """
    Checks for missing columns and adds them if necessary.
    """
    logger.info("Starting comprehensive schema check...")
    
    try:
        inspector = inspect(engine)
        
        # 1. Check User table columns
        user_columns = [col['name'] for col in inspector.get_columns('users')]
        logger.info(f"Current User columns: {user_columns}")
        
        with engine.connect() as conn:
            # Add payment_method
            if 'payment_method' not in user_columns:
                logger.info("Adding payment_method to users...")
                conn.execute(text("ALTER TABLE users ADD COLUMN payment_method VARCHAR"))
            
            # Add stripe_customer_id
            if 'stripe_customer_id' not in user_columns:
                logger.info("Adding stripe_customer_id to users...")
                conn.execute(text("ALTER TABLE users ADD COLUMN stripe_customer_id VARCHAR"))
                
            # Add stripe_subscription_id
            if 'stripe_subscription_id' not in user_columns:
                logger.info("Adding stripe_subscription_id to users...")
                conn.execute(text("ALTER TABLE users ADD COLUMN stripe_subscription_id VARCHAR"))
                
            # Add session_string
            if 'session_string' not in user_columns:
                logger.info("Adding session_string to users...")
                conn.execute(text("ALTER TABLE users ADD COLUMN session_string VARCHAR"))
                
            # Add referral_code (just in case)
            if 'referral_code' not in user_columns:
                logger.info("Adding referral_code to users...")
                conn.execute(text("ALTER TABLE users ADD COLUMN referral_code VARCHAR UNIQUE"))
                conn.execute(text("CREATE INDEX ix_users_referral_code ON users (referral_code)"))
                
            # Add referred_by (just in case)
            if 'referred_by' not in user_columns:
                logger.info("Adding referred_by to users...")
                conn.execute(text("ALTER TABLE users ADD COLUMN referred_by VARCHAR REFERENCES users(phone) ON DELETE SET NULL"))
                
            # Add referral_count (just in case)
            if 'referral_count' not in user_columns:
                logger.info("Adding referral_count to users...")
                conn.execute(text("ALTER TABLE users ADD COLUMN referral_count INTEGER DEFAULT 0"))

            # 2. Check Feed table columns
            feed_columns = [col['name'] for col in inspector.get_columns('feeds')]
            logger.info(f"Current Feed columns: {feed_columns}")
            
            # Add filters
            if 'filters' not in feed_columns:
                logger.info("Adding filters to feeds...")
                conn.execute(text("ALTER TABLE feeds ADD COLUMN filters JSON"))
                
            # Add source_filters
            if 'source_filters' not in feed_columns:
                logger.info("Adding source_filters to feeds...")
                conn.execute(text("ALTER TABLE feeds ADD COLUMN source_filters JSON"))
                
            # Add delay_enabled
            if 'delay_enabled' not in feed_columns:
                logger.info("Adding delay_enabled to feeds...")
                conn.execute(text("ALTER TABLE feeds ADD COLUMN delay_enabled BOOLEAN DEFAULT TRUE"))
                
            # Add error
            if 'error' not in feed_columns:
                logger.info("Adding error to feeds...")
                conn.execute(text("ALTER TABLE feeds ADD COLUMN error VARCHAR"))

            conn.commit()
            logger.info("✓ Schema check and update completed!")
                
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    run_migration()
