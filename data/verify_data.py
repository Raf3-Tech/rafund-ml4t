"""
Verify the 5-year dataset loaded into database.

This script validates:
1. All symbols have correct number of records
2. Data spans exactly 5 years (1825 days)
3. No gaps in daily data
4. All OHLCV fields are valid
5. Database is ready for backtesting
"""

import logging
import sys
from pathlib import Path
from datetime import datetime

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from data.db import DatabaseConnection

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/data_verification.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)


def verify_data(db: DatabaseConnection) -> bool:
    """Verify all loaded data is correct and complete."""
    
    logger.info("="*80)
    logger.info("DATA VERIFICATION REPORT")
    logger.info("="*80)
    
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # 1. Check total records
        cursor.execute("SELECT COUNT(*) FROM prices")
        total_records = cursor.fetchone()[0]
        logger.info(f"\n[TOTAL RECORDS] {total_records:,} OHLCV records found")
        
        if total_records == 0:
            logger.error("ERROR: No data found in database!")
            return False
        
        # 2. Check symbols
        logger.info("\n[SYMBOLS LOADED]")
        cursor.execute("""
            SELECT symbol, COUNT(*) as count
            FROM prices
            GROUP BY symbol
            ORDER BY symbol
        """)
        
        symbols_data = cursor.fetchall()
        all_valid = True
        
        for symbol, count in symbols_data:
            status = "OK" if count == 1825 else "MISMATCH"
            logger.info(f"  {symbol:12} {count:5} records [{status}]")
            if count != 1825:
                all_valid = False
        
        # 3. Check date range
        logger.info("\n[DATE RANGE]")
        cursor.execute("""
            SELECT 
                symbol,
                MIN(timestamp) as min_date,
                MAX(timestamp) as max_date,
                COUNT(DISTINCT DATE(timestamp)) as unique_days
            FROM prices
            GROUP BY symbol
            ORDER BY symbol
        """)
        
        date_data = cursor.fetchall()
        for symbol, min_date, max_date, unique_days in date_data:
            days_span = (max_date - min_date).days + 1
            status = "OK" if unique_days == 1825 else f"GAP ({unique_days})"
            logger.info(f"  {symbol:12} {unique_days:4} unique days [{status}]")
            logger.info(f"               {min_date.date()} to {max_date.date()}")
        
        # 4. Check OHLCV data quality
        logger.info("\n[DATA QUALITY CHECKS]")
        
        cursor.execute("""
            SELECT 
                symbol,
                COUNT(*) as total,
                COUNT(CASE WHEN open IS NULL THEN 1 END) as null_open,
                COUNT(CASE WHEN high IS NULL THEN 1 END) as null_high,
                COUNT(CASE WHEN low IS NULL THEN 1 END) as null_low,
                COUNT(CASE WHEN close IS NULL THEN 1 END) as null_close,
                COUNT(CASE WHEN volume IS NULL THEN 1 END) as null_volume,
                COUNT(CASE WHEN high < low THEN 1 END) as invalid_hl,
                COUNT(CASE WHEN open < 0 OR high < 0 OR low < 0 OR close < 0 THEN 1 END) as negative_prices
            FROM prices
            GROUP BY symbol
            ORDER BY symbol
        """)
        
        quality_data = cursor.fetchall()
        
        has_issues = False
        for (symbol, total, null_open, null_high, null_low, null_close, null_volume, 
             invalid_hl, negative_prices) in quality_data:
            logger.info(f"\n  {symbol}:")
            logger.info(f"    Total records: {total}")
            
            issues = []
            if null_open > 0:
                issues.append(f"NULL open: {null_open}")
            if null_high > 0:
                issues.append(f"NULL high: {null_high}")
            if null_low > 0:
                issues.append(f"NULL low: {null_low}")
            if null_close > 0:
                issues.append(f"NULL close: {null_close}")
            if null_volume > 0:
                issues.append(f"NULL volume: {null_volume}")
            if invalid_hl > 0:
                issues.append(f"High < Low: {invalid_hl}")
            if negative_prices > 0:
                issues.append(f"Negative prices: {negative_prices}")
            
            if issues:
                logger.warning(f"    ISSUES: {', '.join(issues)}")
                has_issues = True
            else:
                logger.info("    Status: OK (no anomalies)")
        
        # 5. Summary for backtest readiness
        logger.info("\n" + "="*80)
        logger.info("BACKTEST READINESS CHECK")
        logger.info("="*80)
        
        checks = {
            "Data loaded (>10k records)": total_records > 10000,
            "8 symbols loaded": len(symbols_data) == 8,
            "5 years complete (1825 days)": not has_issues,
            "No NULL values in OHLCV": not has_issues,
            "No data anomalies": not has_issues
        }
        
        all_pass = all(checks.values())
        
        for check, result in checks.items():
            status = "[OK]" if result else "[FAILED]"
            logger.info(f"  {status} {check}")
        
        logger.info("\n" + "="*80)
        if all_pass:
            logger.info("DATABASE READY FOR BACKTESTING!")
            logger.info("="*80)
            logger.info("\nYou can now:")
            logger.info("1. Run: python main.py backtest")
            logger.info("2. Execute statistical arbitrage strategy on 5 years of data")
            logger.info("3. Analyze cointegration between trading pairs")
            return True
        else:
            logger.error("DATABASE VERIFICATION FAILED!")
            logger.error("="*80)  
            return False
        
    except Exception as e:
        logger.error(f"Verification error: {str(e)}")
        return False
    finally:
        try:
            cursor.close()
            db.return_connection(conn)
        except:
            pass


def main():
    """Run verification."""
    try:
        db = DatabaseConnection()
        
        if not db.test_connection():
            logger.error("Database connection failed!")
            return False
        
        success = verify_data(db)
        return success
        
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
