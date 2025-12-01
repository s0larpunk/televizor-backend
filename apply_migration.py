from database import engine
from sqlalchemy import text
import logging
import sys

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def run_migration():
    """
    Applies the migration to change Integer columns to BigInteger.
    """
    logger.info("Starting database migration...")
    
    try:
        with engine.connect() as conn:
            # Check if we are using PostgreSQL
            if 'postgresql' in str(engine.url):
                logger.info("Detected PostgreSQL database.")
                
                # Migrate users table
                logger.info("Migrating users.telegram_id to BIGINT...")
                conn.execute(text("ALTER TABLE users ALTER COLUMN telegram_id TYPE BIGINT"))
                
                # Migrate feeds table
                logger.info("Migrating feeds.destination_channel_id to BIGINT...")
                conn.execute(text("ALTER TABLE feeds ALTER COLUMN destination_channel_id TYPE BIGINT"))
                
                conn.commit()
                logger.info("✓ Migration completed successfully!")
            else:
                logger.warning("Not using PostgreSQL. SQLite does not support easy column type alteration via SQL.")
                logger.warning("If you are using SQLite, you might need to recreate the tables or use a tool like Alembic.")
                
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    run_migration()
