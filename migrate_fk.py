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
    Applies the foreign key fix migration.
    """
    logger.info("Starting foreign key fix migration...")
    
    try:
        with engine.connect() as conn:
            # Read SQL file
            with open("migration_fk_fix.sql", "r") as f:
                sql = f.read()
            
            logger.info(f"Executing SQL: {sql}")
            conn.execute(text(sql))
            conn.commit()
            logger.info("✓ Migration completed successfully!")
                
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    run_migration()
