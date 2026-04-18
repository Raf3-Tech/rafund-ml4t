# ML4T Data Collection Guide

## Overview

The ML4T system uses a robust data collection pipeline to fetch market data from Binance and store it in PostgreSQL. This guide explains how to set everything up and start collecting data.

---

## Prerequisites

### 1. PostgreSQL Database

You've already created the `rafund` database with the schema. Verify it's running:

```bash
# Connect to PostgreSQL
psql -U postgres -d rafund

# Verify tables exist
\dt
```

You should see:
- `prices` - OHLCV data
- `features` - Calculated spreads and z-scores
- `signals` - Trading signals
- `trades` - Trade records
- `portfolio` - Portfolio snapshots
- `backtest_results` - Backtest metrics

### 2. Python Dependencies

Install required packages:

```bash
pip install -r requirements.txt
```

Key packages:
- **ccxt**: Unified API for 100+ exchanges (we use Binance)
- **psycopg2**: PostgreSQL adapter for Python
- **pandas**: Data manipulation
- **sklearn**: Machine learning (future use)

---

## System Architecture

### Data Collection Flow

```
┌─────────────┐
│   Binance   │
│  Exchange   │
└──────┬──────┘
       │ (CCXT)
       ▼
┌─────────────────────────────┐
│  BinanceCollector           │
│  - Fetch OHLCV data         │
│  - Rate limiting            │
│  - Data validation          │
└──────┬──────────────────────┘
       │
       ▼
┌─────────────────────────────┐
│  DatabaseConnection         │
│  - Insert into DB           │
│  - Query historical data    │
│  - Connection pooling       │
└──────┬──────────────────────┘
       │
       ▼
┌─────────────┐
│  PostgreSQL │
│  (rafund)   │
└─────────────┘
```

### Key Modules

#### `data/collectors/binance_collector.py`
**BinanceCollector class** - Handles all Binance API interactions

**Main methods:**
```python
collector = BinanceCollector(testnet=False)

# Fetch single batch (max 1000 candles)
df = collector.fetch_ohlcv('BTC/USDT', '1d', limit=100)

# Fetch entire history with pagination
df = collector.fetch_ohlcv_history('BTC/USDT', '1d', start_date, end_date)

# Validate data quality
is_valid = collector.validate_data(df)

# Get market info
info = collector.get_market_info('BTC/USDT')
```

#### `data/db.py`
**DatabaseConnection class** - PostgreSQL connection pooling and queries

**Main methods:**
```python
db = DatabaseConnection(
    host='localhost',
    port=5432,
    database='rafund',
    user='postgres',
    password='postgres'
)

# Insert OHLCV data
inserted = db.insert_prices(df)

# Retrieve data
df = db.get_prices('BTC/USDT', start_date, end_date)

# Get latest timestamp
last_ts = db.get_latest_timestamp('BTC/USDT')

# Database stats
stats = db.get_data_stats()
```

---

## How to Collect Data

### Option 1: Run Main Script (Easiest)

```bash
# Collect data from Binance
python main.py collect

# This will:
# 1. Connect to Binance
# 2. Fetch 1 year of daily data for 4 symbols
# 3. Validate data quality
# 4. Insert into PostgreSQL
# 5. Print summary stats
```

**Expected output:**
```
============================================================
Collecting data for BTC/USDT
============================================================
Fetching 1000 1d candles for BTC/USDT
Successfully fetched 365 candles for BTC/USDT
Inserted 365 price records into database
✓ BTC/USDT: 365 records inserted

[Similar for ETH/USDT, SOL/USDT, BNB/USDT]

Database now contains:
  Total records: 1460
  Total symbols: 4
  Date range: 2023-04-17 to 2024-04-16
```

### Option 2: Custom Python Script

```python
from data.collectors.binance_collector import BinanceCollector
from data.db import DatabaseConnection
from datetime import datetime, timedelta

# Initialize
collector = BinanceCollector(testnet=False)
db = DatabaseConnection(
    host='localhost',
    port=5432,
    database='rafund',
    user='postgres',
    password='postgres'
)

# Define date range
end_date = datetime.utcnow()
start_date = end_date - timedelta(days=365)

# Fetch and store data
df = collector.fetch_ohlcv_history('BTC/USDT', '1d', start_date, end_date)

if collector.validate_data(df):
    inserted = db.insert_prices(df)
    print(f"Inserted {inserted} records")

db.close_pool()
```

### Option 3: Incremental Updates

Update with only new data:

```python
from data.collectors.binance_collector import BinanceCollector
from data.db import DatabaseConnection
from datetime import datetime, timedelta

collector = BinanceCollector()
db = DatabaseConnection()

# Get latest timestamp from DB
last_ts = db.get_latest_timestamp('BTC/USDT')

# Fetch data from last timestamp to now
df = collector.fetch_ohlcv_history(
    'BTC/USDT',
    '1d',
    start_date=last_ts,
    end_date=datetime.utcnow()
)

# Insert only new data
db.insert_prices(df)
db.close_pool()
```

---

## Data Quality & Validation

The system performs several validations:

### Automatic Checks

✓ **Positive prices** - No negative OHLC values
✓ **Logical consistency** - High ≥ Low, High ≥ Close ≥ Low
✓ **No NaN values** - All fields populated
✓ **Correct timestamps** - In chronological order

### Viewing Data Quality

```python
from data.db import DatabaseConnection

db = DatabaseConnection()
stats = db.get_data_stats()

print(f"Total records: {stats['total_price_records']}")
print(f"Total symbols: {stats['num_symbols']}")
print(f"Date range: {stats['min_date']} to {stats['max_date']}")

db.close_pool()
```

---

## Database Query Examples

### View Raw Data

```sql
-- Last 10 days of BTC prices
SELECT timestamp, open, high, low, close, volume 
FROM prices 
WHERE symbol = 'BTC/USDT'
ORDER BY timestamp DESC
LIMIT 10;

-- All symbols in database
SELECT DISTINCT symbol FROM prices ORDER BY symbol;

-- Data count by symbol
SELECT symbol, COUNT(*) as records 
FROM prices 
GROUP BY symbol 
ORDER BY records DESC;
```

### Connect from Python

```python
from data.db import DatabaseConnection
import pandas as pd

db = DatabaseConnection()

# Get 100 days of BTC data
df = db.get_prices('BTC/USDT', 
                   start_date='2024-01-01',
                   end_date='2024-04-10')

print(df.head())
print(df.describe())

db.close_pool()
```

---

## Troubleshooting

### Database Connection Failed

```
Error: could not connect to server: Connection refused
```

**Solution**: Make sure PostgreSQL is running

```bash
# Windows: Check Services
Get-Service PostgreSQL94

# Or start PostgreSQL manually
```

### API Rate Limit Exceeded

```
Error: 429 Too Many Requests
```

**Solution**: Increase `rate_limit_ms` in BinanceCollector

```python
# Default is 100ms, increase to 200ms
collector = BinanceCollector(rate_limit_ms=200)
```

### Symbol Not Found

```
Error: ccxt.ExchangeError: symbol BTC-USD not found
```

**Solution**: Use correct Binance format (with `/USDT`)

```python
# Correct
df = collector.fetch_ohlcv_history('BTC/USDT', '1d')

# Wrong
df = collector.fetch_ohlcv_history('BTC-USD', '1d')
```

### Duplicate Key Error

```
Error: duplicate key value violates unique constraint
```

**Solution**: This is normal! UPSERT prevents duplicates. Schema has:
```sql
ON CONFLICT (symbol, timestamp) DO NOTHING
```

---

## Next Steps

Once you have data collected:

1. **Calculate Features** - `features/factor_models.py`
   - Compute spreads between assets
   - Calculate z-scores
   - Identify cointegrated pairs

2. **Generate Signals** - `strategies/stat_arb.py`
   - Use z-scores to generate buy/sell signals
   - Track positions over time

3. **Run Backtest** - `backtesting/engine.py`
   - Simulate strategy on historical data
   - Calculate metrics (Sharpe, drawdown, etc.)

4. **Monitor Performance** - `monitoring/metrics.py`
   - Track live P&L
   - Validate strategy robustness

---

## Configuration

### Database Connection Details

File: `main.py` or use environment variables

```python
db = DatabaseConnection(
    host='localhost',        # PostgreSQL host
    port=5432,               # PostgreSQL port
    database='rafund',       # Database name
    user='postgres',         # Username
    password='postgres',     # Change to your password!
    min_conn=1,              # Min pooled connections
    max_conn=5               # Max pooled connections
)
```

### Binance Collector Settings

```python
collector = BinanceCollector(
    testnet=False,          # Use mainnet (True = testnet)
    rate_limit_ms=100       # Delay between API calls (ms)
)
```

---

## Performance Tips

### Speed Up Collection

1. **Parallel symbol collection** (future):
   ```python
   # Use ThreadPoolExecutor to fetch multiple symbols simultaneously
   ```

2. **Use higher timeframe initially**:
   ```python
   # Start with 1d (1440 data points/year)
   # 1h has 8760 data points/year (slower)
   ```

3. **Batch limit carefully**:
   ```python
   # Binance allows 1000 candles per call
   # Fewer calls = faster, but more API pressure
   df = collector.fetch_ohlcv(symbol, '1d', limit=1000)
   ```

### Database Performance

1. **Index optimization** - Already done in schema.sql
   - Indexes on (symbol, timestamp)
   - Essential for fast queries

2. **VACUUM & ANALYZE** (monthly):
   ```sql
   VACUUM ANALYZE prices;
   ```

3. **Monitor pool usage**:
   ```python
   # Set appropriate min/max connections
   DatabaseConnection(min_conn=1, max_conn=5)
   ```

---

## Logging

All operations are logged to `logs/ml4t.log`:

```
2024-04-16 14:32:15,123 - BinanceCollector - INFO - Binance client initialized
2024-04-16 14:32:16,456 - BinanceCollector - INFO - Fetching 100 1d candles for BTC/USDT
2024-04-16 14:32:22,789 - BinanceCollector - INFO - Successfully fetched 365 candles
2024-04-16 14:32:23,012 - DatabaseConnection - INFO - Inserted 365 price records
```

View logs in real-time:
```bash
tail -f logs/ml4t.log
```

---

## FAQ

**Q: How often should I update data?**
A: Daily for a daily strategy. Use incremental updates via `get_latest_timestamp()`.

**Q: Can I collect data for multiple symbols in parallel?**
A: Currently sequential. Future: Use ThreadPoolExecutor to parallelize.

**Q: What's the maximum lookback period?**
A: Binance has unlimited history for 1d candles. Rate limits are the bottleneck.

**Q: Why PostgreSQL over CSV or SQLite?**
A: Production-grade:
- Concurrent access
- ACID transactions
- Indexing for speed
- Connection pooling
- Complex queries

**Q: How do I connect from Excel/Tableau?**
A: PostgreSQL has ODBC drivers for reporting tools.

---

**You're now ready to collect market data! Run:**

```bash
python main.py collect
```

The system will fetch 365 days of data for BTC/ETH/SOL/BNB and populate your database.

