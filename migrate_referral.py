from database import engine
from sqlalchemy import text
import logging
import sys
import os

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def run_migration():
    """
    Applies the referral migration (adding referral_code to web_sessions).
    """
    logger.info("Starting referral migration...")
    
    try:
        with engine.connect() as conn:
            # Read SQL file
            with open("migration_referral.sql", "r") as f:
                sql = f.read()
            
            logger.info(f"Executing SQL: {sql}")
            conn.execute(text(sql))
            conn.commit()
            logger.info("✓ Migration completed successfully!")
                
    except Exception as e:
        if "duplicate column name" in str(e).lower() or "already exists" in str(e).lower():
            logger.info("Column already exists, skipping.")
        else:
            logger.error(f"Migration failed: {e}")
            sys.exit(1)

if __name__ == "__main__":
    run_migration()
