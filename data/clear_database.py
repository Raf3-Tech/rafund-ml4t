"""
Clear all data from database tables.

This script safely clears all price data while preserving the schema.
Use this before reloading fresh data.
"""

import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from data.db import DatabaseConnection

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/data_operations.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)


def clear_database(db: DatabaseConnection) -> bool:
    """
    Clear all data from database tables while preserving schema.
    
    Args:
        db: DatabaseConnection instance
        
    Returns:
        True if successful, False otherwise
    """
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # Tables to clear (order matters due to constraints)
        tables = [
            'backtest_results',
            'portfolio',
            'trades',
            'signals',
            'features',
            'prices'
        ]
        
        logger.info("="*80)
        logger.info("CLEARING DATABASE TABLES")
        logger.info("="*80)
        
        for table in tables:
            try:
                # Delete all data from the table
                cursor.execute(f"DELETE FROM {table};")
                deleted = cursor.rowcount
                logger.info(f"[OK] Cleared {table}: {deleted} rows deleted")
            except Exception as e:
                logger.error(f"[ERROR] Error clearing {table}: {str(e)}")
                conn.rollback()
                cursor.close()
                db.return_connection(conn)
                return False
        
        # Commit the deletion
        conn.commit()
        
        # Get table statistics
        logger.info("\n" + "="*80)
        logger.info("TABLE STATISTICS AFTER CLEARING")
        logger.info("="*80)
        
        for table in tables:
            cursor.execute(f"SELECT COUNT(*) FROM {table};")
            count = cursor.fetchone()[0]
            logger.info(f"{table}: {count} rows")
        
        cursor.close()
        db.return_connection(conn)
        
        logger.info("\n[OK] Database cleared successfully!")
        return True
        
    except Exception as e:
        logger.error(f"Error clearing database: {str(e)}")
        return False


def main():
    """Main clear database operation."""
    
    logger.info("="*80)
    logger.info("DATABASE CLEAR OPERATION")
    logger.info("="*80)
    
    try:
        # Initialize database connection
        logger.info("Connecting to database...")
        db = DatabaseConnection()
        
        # Test connection
        if not db.test_connection():
            logger.error("[ERROR] Database connection failed. Exiting.")
            return False
        
        # Confirm operation
        logger.warning("\n[WARNING] This will delete ALL data from the database!")
        logger.warning("[WARNING] Make sure this is what you want to do!")
        
        response = input("\nType 'YES' to confirm deletion: ").strip().upper()
        
        if response != 'YES':
            logger.info("Operation cancelled.")
            return True
        
        # Clear database
        success = clear_database(db)
        
        if success:
            logger.info("\n✓ All data successfully cleared!")
            logger.info("Ready to load new data.")
            return True
        else:
            logger.error("\n[ERROR] Database clearing failed!")
            return False
            
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
        return False
    finally:
        if 'db' in locals():
            db.pool.closeall()


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
