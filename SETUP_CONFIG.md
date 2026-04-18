"""
ML4T Configuration & Setup

This file documents all configuration options and how to set them up.
"""

# ============================================================================
# DATABASE CONFIGURATION
# ============================================================================

# PostgreSQL Connection Settings
# Location: main.py, data/db.py, or environment variables

DATABASE_CONFIG = {
    'host': 'localhost',           # PostgreSQL server hostname
    'port': 5432,                  # PostgreSQL server port (default: 5432)
    'database': 'rafund',          # Database name (you created this)
    'user': 'postgres',            # Database user (default: postgres)
    'password': 'postgres',        # Database password (CHANGE THIS!)
    'min_conn': 1,                 # Min connections in pool
    'max_conn': 5                  # Max connections in pool
}

"""
HOW TO CONFIGURE:

Option 1: Direct in code
-----------------------
from data.db import DatabaseConnection

db = DatabaseConnection(
    host='localhost',
    port=5432,
    database='rafund',
    user='postgres',
    password='YOUR_PASSWORD_HERE'  # Change this!
)

Option 2: Environment variables (recommended for security)
-----------------------------------------------------------
# Create .env file in project root:
DB_HOST=localhost
DB_PORT=5432
DB_NAME=rafund
DB_USER=postgres
DB_PASSWORD=your_secret_password

# Then in code:
import os
from dotenv import load_dotenv

load_dotenv()
db = DatabaseConnection(
    host=os.getenv('DB_HOST'),
    port=int(os.getenv('DB_PORT')),
    database=os.getenv('DB_NAME'),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD')
)

IMPORTANT: Add .env to .gitignore to avoid committing passwords!
"""

# ============================================================================
# BINANCE COLLECTOR CONFIGURATION
# ============================================================================

BINANCE_CONFIG = {
    'testnet': False,              # True = testnet (paper trading)
                                   # False = mainnet (real API)
    'rate_limit_ms': 100,          # Milliseconds between API calls
                                   # Increase if hitting rate limits
    'timeout': 30000               # API call timeout in milliseconds
}

"""
HOW TO CONFIGURE:

from data.collectors.binance_collector import BinanceCollector

# Mainnet (real data, no API key needed)
collector = BinanceCollector(
    testnet=False,
    rate_limit_ms=100
)

# Testnet (for testing, no real money)
collector = BinanceCollector(
    testnet=True,
    rate_limit_ms=50
)

RATE LIMITING:
    100ms = Default (safe)
    50ms  = Faster (may hit limits)
    200ms = Slower (safe for many symbols)
    500ms = Very safe (but slow)
"""

# ============================================================================
# STRATEGY CONFIGURATION
# ============================================================================

STRATEGY_CONFIG = {
    'entry_threshold': 2.0,        # Z-score for entry signal
    'exit_threshold': 0.5,         # Z-score for exit signal
    'lookback_period': 60,         # Days for rolling stats
    'max_position_size': 10000,    # Max units per trade
    'max_allocation_pct': 0.20,    # Max 20% of capital per position
    'risk_limit': 0.02,            # Max 2% loss per trade
    'stop_loss_pct': 0.05,         # 5% hard stop loss
    'take_profit_pct': 0.10        # 10% take profit target
}

"""
HOW TO CONFIGURE:

Edit config/settings.yaml:

strategy:
  entry_threshold: 2.0      # More extreme = fewer trades
  exit_threshold: 0.5       # Tighter = faster exits
  lookback_period: 60       # Longer = more stable signals
  max_position_size: 10000
  max_allocation_pct: 0.20
"""

# ============================================================================
# PORTFOLIO CONFIGURATION
# ============================================================================

PORTFOLIO_CONFIG = {
    'initial_capital': 100000,     # Starting capital in USDT
    'trading_pairs': [
        'BTC/USDT',
        'ETH/USDT',
        'SOL/USDT',
        'BNB/USDT'
    ],
    'rebalance_frequency': 'daily',
    'rebalance_time': '09:00:00'   # UTC
}

"""
HOW TO CONFIGURE:

Edit config/settings.yaml or main.py:

portfolio:
  initial_capital: 100000
  trading_pairs:
    - BTC/USDT
    - ETH/USDT
    - SOL/USDT
    - BNB/USDT
"""

# ============================================================================
# BACKTESTING CONFIGURATION
# ============================================================================

BACKTEST_CONFIG = {
    'start_date': '2023-01-01',
    'end_date': '2024-12-31',
    'initial_capital': 100000,
    'commission': 0.001,           # 0.1% per trade
    'slippage': 0.0001             # Price impact
}

"""
HOW TO CONFIGURE:

Edit config/settings.yaml:

backtesting:
  start_date: 2023-01-01
  end_date: 2024-12-31
  initial_capital: 100000
  commission: 0.001
  slippage: 0.0001
"""

# ============================================================================
# SETUP STEPS
# ============================================================================

"""
1. CREATE POSTGRESQL DATABASE
   =============================
   
   # Connect to PostgreSQL
   psql -U postgres
   
   # Create database
   CREATE DATABASE rafund;
   
   # Connect to new database
   \c rafund
   
   # Create schema
   \i data/schema.sql
   
   # Verify tables
   \dt
   
   Expected output:
   Did not find any relations.
   (then after \i data/schema.sql:)
   
            List of relations
    Schema |      Name       | Type  | Owner
   --------+-----------------+-------+----------
    public | backtest_results| table | postgres
    public | features        | table | postgres
    public | portfolio       | table | postgres
    public | prices          | table | postgres
    public | signals         | table | postgres
    public | trades          | table | postgres

2. INSTALL PYTHON DEPENDENCIES
   =============================
   
   pip install -r requirements.txt
   
   Verify installation:
   python -c "import ccxt; import psycopg2; print('OK')"
   
   Expected output: OK

3. UPDATE DATABASE PASSWORD
   =============================
   
   Recommended: Create .env file
   
   Create file: .env
   
   DB_HOST=localhost
   DB_PORT=5432
   DB_NAME=rafund
   DB_USER=postgres
   DB_PASSWORD=your_postgres_password
   
   Then update main.py:
   
   import os
   from dotenv import load_dotenv
   
   load_dotenv()
   
   db = DatabaseConnection(
       host=os.getenv('DB_HOST'),
       port=int(os.getenv('DB_PORT')),
       database=os.getenv('DB_NAME'),
       user=os.getenv('DB_USER'),
       password=os.getenv('DB_PASSWORD')
   )

4. CONFIGURE SYMBOLS & DATES
   ==========================
   
   Edit main.py:
   
   symbols = [       # Add/remove symbols
       'BTC/USDT',
       'ETH/USDT',
       'SOL/USDT',
       'BNB/USDT'
   ]
   
   start_date = datetime.utcnow() - timedelta(days=365)  # 1 year
   end_date = datetime.utcnow()

5. VERIFY SETUP
   ==============
   
   python test_setup.py
   
   Expected: All tests pass ✓

6. COLLECT DATA
   ==============
   
   python main.py collect
   
   Expected: Collects data for all symbols

7. VERIFY DATA
   ==============
   
   psql -U postgres -d rafund -c "SELECT COUNT(*) FROM prices;"
   
   Expected: 1460+ records
"""

# ============================================================================
# COMMON CONFIGURATIONS
# ============================================================================

"""
CONSERVATIVE STRATEGY
    - Entry: z > 3.0 (very extreme)
    - Exit: z < 0.5
    - Lookback: 90 days
    - Max position: 5% of capital
    - Few trades, high win rate

AGGRESSIVE STRATEGY
    - Entry: z > 1.5 (slight deviation)
    - Exit: z < 0.0
    - Lookback: 30 days
    - Max position: 30% of capital
    - Many trades, lower win rate

MEDIUM STRATEGY (DEFAULT)
    - Entry: z > 2.0
    - Exit: z < 0.5
    - Lookback: 60 days
    - Max position: 20% of capital
    - Balanced risk/reward
"""

# ============================================================================
# SECURITY BEST PRACTICES
# ============================================================================

"""
1. NEVER commit passwords to git
   
   Add to .gitignore:
   .env
   *.secret
   credentials.json

2. Use environment variables for sensitive data
   
   from dotenv import load_dotenv
   password = os.getenv('DB_PASSWORD')

3. Rotate database passwords regularly
   
   ALTER USER postgres WITH PASSWORD 'new_password';

4. Restrict PostgreSQL access
   
   Edit pg_hba.conf:
   local   all             postgres                                trust
   local   all             all                                     md5
   host    all             all             127.0.0.1/32            md5

5. Use firewall for production
   
   Only allow PostgreSQL from application server
   Close port 5432 to public internet
"""

# ============================================================================
# LOGGING CONFIGURATION
# ============================================================================

LOG_CONFIG = {
    'level': 'INFO',               # DEBUG, INFO, WARNING, ERROR
    'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    'file': 'logs/ml4t.log'
}

"""
View logs:
    
    tail -f logs/ml4t.log            # Linux/Mac
    Get-Content logs/ml4t.log -Wait  # Windows PowerShell
    
Change log level in logging.basicConfig():
    
    level=logging.DEBUG   # Very verbose
    level=logging.INFO    # Normal
    level=logging.WARNING # Only warnings/errors
"""

# ============================================================================
# HELP & TROUBLESHOOTING
# ============================================================================

"""
COMMON ERRORS:

1. "connection refused"
   → PostgreSQL not running
   → Start: net start postgresql-x64-14
   
2. "authentication failed"
   → Wrong password
   → Update database password
   
3. "database does not exist"
   → Database not created
   → Run: CREATE DATABASE rafund;
   
4. "no module named ccxt"
   → Dependency not installed
   → Run: pip install -r requirements.txt
   
5. "429 Too Many Requests"
   → Hitting Binance rate limit
   → Increase rate_limit_ms to 200-500

See DATA_COLLECTION.md for full troubleshooting guide.
"""
