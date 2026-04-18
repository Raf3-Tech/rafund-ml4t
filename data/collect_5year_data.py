"""
5-Year Historical Data Collection Pipeline

Collects 5 years of daily OHLCV data for multiple crypto pairs suitable
for statistical arbitrage trading strategies.

Symbol Selection:
- Major pairs with high liquidity and long history on Binance
- Selected for statistical arbitrage potential (cointegration analysis)
- Includes pairs from different sectors for diversification
"""

import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from data.collectors.binance_collector import BinanceCollector
from data.db import DatabaseConnection

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/data_collection_5year.log'),
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
        timeframe: Candle timeframe ('1d' for daily)
        start_date: Start date for historical data
        end_date: End date for historical data
        
    Returns:
        True if successful, False otherwise
    """
    try:
        logger.info(f"\n{'='*70}")
        logger.info(f"Collecting 5-year data for {symbol}")
        logger.info(f"Period: {start_date.date()} to {end_date.date()}")
        logger.info(f"{'='*70}")
        
        # Fetch historical data
        df = collector.fetch_ohlcv_history(symbol, timeframe, start_date, end_date)
        
        if df.empty:
            logger.error(f"[ERROR] No data fetched for {symbol}")
            return False
        
        logger.info(f"Fetched {len(df)} candles for {symbol}")
        
        # Validate data
        if not collector.validate_data(df):
            logger.error(f"[ERROR] Data validation failed for {symbol}")
            return False
        
        logger.info(f"[OK] Data validation passed for {symbol}")
        
        # Insert into database
        inserted = db.insert_prices(df)
        
        if inserted > 0:
            logger.info(f"[OK] Successfully inserted {inserted} records for {symbol}")
            return True
        else:
            logger.warning(f"[WARNING] No new records inserted for {symbol} (may be duplicates)")
            return True
    
    except Exception as e:
        logger.error(f"[ERROR] Error collecting data for {symbol}: {str(e)}")
        return False


def main():
    """Main 5-year data collection pipeline."""
    
    logger.info("="*80)
    logger.info("ML4T 5-YEAR DATA COLLECTION PIPELINE")
    logger.info(f"Started at {datetime.now().isoformat()}")
    logger.info("="*80)
    
    try:
        # Initialize Binance collector (mainnet, with rate limiting)
        logger.info("\nInitializing Binance collector...")
        collector = BinanceCollector(testnet=False, rate_limit_ms=200)
        
        # Initialize database connection
        logger.info("Initializing database connection...")
        db = DatabaseConnection()
        
        # Test database connection
        if not db.test_connection():
            logger.error("[ERROR] Database connection failed. Exiting.")
            return False
        
        # Define symbols for 5-year collection
        # Selected pairs with long history, high liquidity, and potential cointegration
        symbols = [
            # Original pairs (major assets)
            'BTC/USDT',   # Bitcoin - reserve cryptocurrency
            'ETH/USDT',   # Ethereum - major smart contract platform
            
            # Additional pairs for statistical arbitrage (4+ more pairs)
            'SOL/USDT',   # Solana - PoS blockchain platform
            'BNB/USDT',   # Binance Coin - layer 1 platform
            'XRP/USDT',   # Ripple - payment network
            'ADA/USDT',   # Cardano - PoS blockchain
            'DOT/USDT',   # Polkadot - multi-chain platform
            'LINK/USDT',  # Chainlink - oracle network
        ]
        
        # Set date range for 5 years of historical data
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=365 * 5)
        
        logger.info(f"\nCollection Period: {start_date.date()} to {end_date.date()}")
        logger.info(f"Total days: {(end_date - start_date).days}")
        logger.info(f"\nSymbols to collect ({len(symbols)} total):")
        for i, sym in enumerate(symbols, 1):
            logger.info(f"   {i}. {sym}")
        
        logger.info(f"\n{'='*80}")
        logger.info("Starting data collection (this may take 10-30 minutes)...")
        logger.info("="*80)
        
        # Collect data for each symbol
        successful = 0
        failed = 0
        results = {}
        
        for idx, symbol in enumerate(symbols, 1):
            logger.info(f"\n[{idx}/{len(symbols)}] Processing {symbol}...")
            
            success = collect_data_for_symbol(
                collector,
                db,
                symbol,
                timeframe='1d',
                start_date=start_date,
                end_date=end_date
            )
            
            results[symbol] = success
            if success:
                successful += 1
                status = "SUCCESS"
            else:
                failed += 1
                status = "FAILED"
            logger.info(f"  {symbol}: {status}")
            
            # Rate limiting between requests
            import time
            time.sleep(0.5)
        
        # Summary
        logger.info(f"\n{'='*80}")
        logger.info("DATA COLLECTION SUMMARY")
        logger.info("="*80)
        logger.info(f"[OK] Successfully collected: {successful}/{len(symbols)}")
        logger.info(f"[ERROR] Failed: {failed}/{len(symbols)}")
        
        logger.info(f"\nDetailed Results:")
        for symbol, success in results.items():
            status = "[OK] SUCCESS" if success else "[ERROR] FAILED"
            logger.info(f"  {symbol}: {status}")
        
        # Get database statistics
        logger.info(f"\n{'='*80}")
        logger.info("DATABASE STATISTICS")
        logger.info("="*80)
        
        try:
            conn = db.get_connection()
            cursor = conn.cursor()
            
            # Count total records by symbol
            cursor.execute("""
                SELECT symbol, COUNT(*) as count
                FROM prices
                GROUP BY symbol
                ORDER BY symbol
            """)
            
            logger.info("\nRecords per symbol:")
            total_records = 0
            for symbol, count in cursor.fetchall():
                logger.info(f"  {symbol}: {count:,} records")
                total_records += count
            
            logger.info(f"\nTotal price records: {total_records:,}")
            
            # Get date range of loaded data
            cursor.execute("""
                SELECT MIN(timestamp) as min_date, MAX(timestamp) as max_date
                FROM prices
            """)
            
            min_date, max_date = cursor.fetchone()
            if min_date and max_date:
                logger.info(f"Date range: {min_date.date()} to {max_date.date()}")
                logger.info(f"Span: {(max_date - min_date).days + 1} days")
            
            cursor.close()
            db.return_connection(conn)
            
        except Exception as e:
            logger.warning(f"Could not retrieve statistics: {str(e)}")
        
        logger.info(f"\n{'='*80}")
        logger.info("[OK] 5-YEAR DATA COLLECTION COMPLETE!")
        logger.info("="*80)
        logger.info("\nNext steps:")
        logger.info("1. Run cointegration analysis to identify best trading pairs")
        logger.info("2. Execute backtest with fixed window strategy")
        logger.info("3. Review statistical arbitrage signal quality")
        
        return failed == 0
            
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
        return False
    finally:
        if 'db' in locals():
            try:
                db.pool.closeall()
            except:
                pass


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
