"""
Test script to verify ML4T system setup.

This script checks:
1. Python dependencies
2. PostgreSQL connection
3. Binance API connectivity
4. Database schema
"""

import sys
import logging
from pathlib import Path
import os

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load .env file manually
def load_env_file():
    """Load .env file manually without dotenv library."""
    env_path = Path('.env')
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    key, value = line.split('=', 1)
                    os.environ[key.strip()] = value.strip()

load_env_file()


def test_imports():
    """Test that all required packages are installed."""
    logger.info("\n" + "="*60)
    logger.info("TEST 1: Checking Python Imports")
    logger.info("="*60)
    
    required_packages = {
        'pandas': 'Data manipulation',
        'numpy': 'Numerical computing',
        'ccxt': 'Exchange API (Binance)',
        'psycopg2': 'PostgreSQL adapter',
        'sklearn': 'Machine learning',
        'fastapi': 'Web framework'
    }
    
    all_ok = True
    for package, description in required_packages.items():
        try:
            if package == 'psycopg2':
                __import__('psycopg2.extensions')
            else:
                __import__(package)
            logger.info(f"✓ {package:15} - {description}")
        except ImportError as e:
            logger.error(f"✗ {package:15} - {description} [NOT INSTALLED]")
            all_ok = False
    
    if not all_ok:
        logger.error("\nInstall missing packages:")
        logger.error("  pip install -r requirements.txt")
    
    return all_ok


def test_database():
    """Test PostgreSQL connection."""
    logger.info("\n" + "="*60)
    logger.info("TEST 2: PostgreSQL Connection")
    logger.info("="*60)
    
    try:
        from data.db import DatabaseConnection
        
        logger.info("Connecting to PostgreSQL...")
        db = DatabaseConnection()
        
        if db.test_connection():
            logger.info("✓ Database connection successful")
            
            # Check tables
            stats = db.get_data_stats()
            logger.info(f"✓ Database stats:")
            logger.info(f"    - Total records: {stats.get('total_price_records', 0)}")
            logger.info(f"    - Total symbols: {stats.get('num_symbols', 0)}")
            
            db.close_pool()
            return True
        else:
            logger.error("✗ Database connection failed")
            logger.info("  (This is OK if PostgreSQL is not running)")
            return False
    
    except Exception as e:
        error_msg = str(e)
        if 'password authentication failed' in error_msg:
            logger.error(f"✗ Database password authentication failed")
            logger.info("  Update password in .env file")
            return False
        elif 'connection refused' in error_msg:
            logger.error(f"✗ PostgreSQL not running or not accessible")
            logger.info("  Start PostgreSQL service or check connection settings")
            return False
        else:
            logger.error(f"✗ Database error: {error_msg}")
            return False


def test_binance():
    """Test Binance API connectivity."""
    logger.info("\n" + "="*60)
    logger.info("TEST 3: Binance API Connectivity")
    logger.info("="*60)
    
    try:
        from data.collectors.binance_collector import BinanceCollector
        
        logger.info("Initializing Binance collector...")
        collector = BinanceCollector(testnet=False)
        
        # Try to get symbols
        logger.info("Fetching symbols from Binance...")
        symbols = collector.get_symbols()
        
        if symbols:
            logger.info(f"✓ Successfully fetched {len(symbols)} symbols")
            logger.info(f"    Sample symbols: {', '.join(symbols[:5])}")
            
            # Try to fetch BTC data (small amount)
            logger.info("Fetching sample OHLCV data (BTC/USDT)...")
            df = collector.fetch_ohlcv('BTC/USDT', '1d', limit=5)
            
            if not df.empty:
                logger.info(f"✓ Successfully fetched {len(df)} candles")
                logger.info(f"    Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")
                return True
            else:
                logger.error("✗ Failed to fetch OHLCV data")
                return False
        else:
            logger.error("✗ Failed to fetch symbols")
            return False
    
    except Exception as e:
        logger.error(f"✗ Binance error: {str(e)}")
        return False


def test_directory_structure():
    """Test that project structure is correct."""
    logger.info("\n" + "="*60)
    logger.info("TEST 4: Directory Structure")
    logger.info("="*60)
    
    required_dirs = [
        'data',
        'data/collectors',
        'data/loaders',
        'features',
        'models',
        'strategies',
        'portfolio',
        'execution',
        'backtesting',
        'monitoring',
        'dashboard',
        'config',
        'logs'
    ]
    
    all_ok = True
    for dir_name in required_dirs:
        dir_path = Path(dir_name)
        if dir_path.exists():
            logger.info(f"✓ {dir_name}")
        else:
            logger.error(f"✗ {dir_name} [MISSING]")
            all_ok = False
    
    return all_ok


def test_data_files():
    """Test that required data files exist."""
    logger.info("\n" + "="*60)
    logger.info("TEST 5: Required Files")
    logger.info("="*60)
    
    required_files = [
        'main.py',
        'requirements.txt',
        'config/settings.yaml',
        'data/schema.sql',
        'data/db.py',
        'data/collectors/binance_collector.py',
        'strategies/stat_arb.py',
        'backtesting/engine.py'
    ]
    
    all_ok = True
    for file_name in required_files:
        file_path = Path(file_name)
        if file_path.exists():
            logger.info(f"✓ {file_name}")
        else:
            logger.error(f"✗ {file_name} [MISSING]")
            all_ok = False
    
    return all_ok


def main():
    """Run all tests."""
    logger.info("\n")
    logger.info("╔════════════════════════════════════════════════════════════╗")
    logger.info("║         ML4T SYSTEM SETUP VERIFICATION                     ║")
    logger.info("╚════════════════════════════════════════════════════════════╝")
    
    results = {
        'Imports': test_imports(),
        'Directory Structure': test_directory_structure(),
        'Files': test_data_files(),
        'PostgreSQL': test_database(),
        'Binance API': test_binance()
    }
    
    # Summary
    logger.info("\n" + "="*60)
    logger.info("SUMMARY")
    logger.info("="*60)
    
    for test_name, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        logger.info(f"{test_name:25} {status}")
    
    all_passed = all(results.values())
    
    logger.info("\n" + "="*60)
    if all_passed:
        logger.info("✓ All tests passed! System is ready.")
        logger.info("\nNext step: Collect market data")
        logger.info("  python main.py collect")
    else:
        logger.error("✗ Some tests failed. Fix issues above.")
    logger.info("="*60 + "\n")
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
