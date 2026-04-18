"""
Data collection pipeline.

This script orchestrates the entire data collection process:
1. Connect to Binance
2. Fetch historical data
3. Validate data
4. Store in PostgreSQL
"""

import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from data.collectors.binance_collector import BinanceCollector
from data.db import DatabaseConnection

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/data_collection.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)


def collect_data_for_symbol(
    collector: BinanceCollector,
    db: DatabaseConnection,
    symbol: str,
    timeframe: str = '1d',
    start_date: datetime = None,
    end_date: datetime = None
) -> bool:
    """
    Collect and store data for a single symbol.
    
    Args:
        collector: BinanceCollector instance
        db: DatabaseConnection instance
        symbol: Trading symbol
        timeframe: Candle timeframe
        start_date: Start date for historical data
        end_date: End date for historical data
        
    Returns:
        True if successful, False otherwise
    """
    try:
        logger.info(f"\n{'='*60}")
        logger.info(f"Collecting data for {symbol}")
        logger.info(f"{'='*60}")
        
        # Fetch historical data
        df = collector.fetch_ohlcv_history(symbol, timeframe, start_date, end_date)
        
        if df.empty:
            logger.error(f"No data fetched for {symbol}")
            return False
        
        # Validate data
        if not collector.validate_data(df):
            logger.error(f"Data validation failed for {symbol}")
            return False
        
        # Insert into database
        inserted = db.insert_prices(df)
        
        if inserted > 0:
            logger.info(f"Successfully inserted {inserted} records for {symbol}")
            return True
        else:
            logger.warning(f"No new records inserted for {symbol} (may be duplicates)")
            return True
    
    except Exception as e:
        logger.error(f"Error collecting data for {symbol}: {str(e)}")
        return False


def main():
    """Main data collection pipeline."""
    
    logger.info("="*80)
    logger.info("ML4T DATA COLLECTION PIPELINE")
    logger.info(f"Started at {datetime.now().isoformat()}")
    logger.info("="*80)
    
    try:
        # Initialize Binance collector (mainnet, with rate limiting)
        logger.info("Initializing Binance collector...")
        collector = BinanceCollector(testnet=False, rate_limit_ms=100)
        
        # Initialize database connection
        logger.info("Initializing database connection...")
        db = DatabaseConnection(
            host='localhost',
            port=5432,
            database='rafund',
            user='postgres',
            password='postgres'  # Change this to your actual password
        )
        
        # Test database connection
        if not db.test_connection():
            logger.error("Database connection failed. Exiting.")
            return False
        
        # Define symbols to collect (starting with major pairs)
        symbols = [
            'BTC/USDT',
            'ETH/USDT',
            'SOL/USDT',
            'BNB/USDT',
            'XRP/USDT',
            'ADA/USDT',
            'DOGE/USDT',
            'LINK/USDT'
        ]
        
        # Set date range for historical data
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=365)  # 1 year of data
        
        logger.info(f"Will collect data from {start_date.date()} to {end_date.date()}")
        logger.info(f"Symbols to collect: {', '.join(symbols)}")
        
        # Collect data for each symbol
        successful = 0
        failed = 0
        
        for symbol in symbols:
            success = collect_data_for_symbol(
                collector,
                db,
                symbol,
                timeframe='1d',
                start_date=start_date,
                end_date=end_date
            )
            
            if success:
                successful += 1
            else:
                failed += 1
        
        # Print summary
        logger.info("\n" + "="*80)
        logger.info("DATA COLLECTION SUMMARY")
        logger.info("="*80)
        logger.info(f"Successfully collected:  {successful}/{len(symbols)} symbols")
        logger.info(f"Failed collections:       {failed}/{len(symbols)} symbols")
        
        # Get database statistics
        stats = db.get_data_stats()
        logger.info(f"\nDatabase Statistics:")
        logger.info(f"  Total records:    {stats.get('total_price_records', 0)}")
        logger.info(f"  Total symbols:    {stats.get('num_symbols', 0)}")
        logger.info(f"  Date range:       {stats.get('min_date')} to {stats.get('max_date')}")
        
        # Close database connection
        db.close_pool()
        
        logger.info("\n" + "="*80)
        logger.info(f"Data collection completed at {datetime.now().isoformat()}")
        logger.info("="*80)
        
        return successful == len(symbols)
    
    except Exception as e:
        logger.error(f"Fatal error in data collection: {str(e)}", exc_info=True)
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
