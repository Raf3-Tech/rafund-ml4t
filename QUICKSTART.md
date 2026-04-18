# ML4T Quick Start Guide

Get your ML4T system running in 5 minutes.

---

## Step 1: Verify Setup (2 minutes)

```bash
# Run the setup verification script
python test_setup.py
```

This checks:
- ✓ Python packages installed
- ✓ PostgreSQL running and connected
- ✓ Binance API reachable
- ✓ Database schema exists
- ✓ All files in place

**Expected output:**
```
SUMMARY
=========================================================
Imports                       ✓ PASS
Directory Structure           ✓ PASS
Files                         ✓ PASS
PostgreSQL                    ✓ PASS
Binance API                   ✓ PASS

✓ All tests passed! System is ready.
```

If any test fails, see [Troubleshooting](#troubleshooting) below.

---

## Step 2: Collect Market Data (3-5 minutes)

```bash
# Fetch 1 year of daily price data from Binance
python main.py collect
```

This does:
1. **Connects to Binance** via CCXT
2. **Fetches data** for BTC/ETH/SOL/BNB (1 year daily)
3. **Validates data** (no negative prices, logical consistency)
4. **Inserts into PostgreSQL** (1,460 total records)

**Expected output:**
```
====================================
Collecting data for BTC/USDT
====================================
Fetching 1000 1d candles for BTC/USDT
Successfully fetched 365 candles for BTC/USDT
✓ BTC/USDT: 365 records inserted

[ETH/USDT, SOL/USDT, BNB/USDT...]

Database now contains:
  Total records: 1460
  Total symbols: 4
  Date range: 2023-04-17 to 2024-04-16
```

**Time to completion:** ~30-60 seconds depending on internet speed

---

## Step 3: Verify Data Collected

```bash
# Option 1: Python script (recommended)
python -c "
from data.db import DatabaseConnection
db = DatabaseConnection()
stats = db.get_data_stats()
print(f'Records: {stats[\"total_price_records\"]}')
print(f'Symbols: {stats[\"num_symbols\"]}')
print(f'Date range: {stats[\"min_date\"]} to {stats[\"max_date\"]}')
db.close_pool()
"

# Option 2: PostgreSQL directly
psql -U postgres -d rafund -c "SELECT symbol, COUNT(*) FROM prices GROUP BY symbol;"
```

**Expected output:**
```
Records: 1460
Symbols: 4
Date range: 2023-04-17 to 2024-04-16
```

---

## What You Now Have

| Component | Status | Location |
|-----------|--------|----------|
| **Database** | ✓ Populated | PostgreSQL (rafund) |
| **Data** | ✓ 1,460 records | prices table |
| **Symbols** | ✓ BTC/ETH/SOL/BNB | 365 days each |
| **Schema** | ✓ 6 tables ready | data/schema.sql |

---

## Next Steps

Now you have foundation data. The pipeline continues:

### 1. Calculate Features (Coming next)
```python
# Calculate spreads and z-scores between pairs
from features.factor_models import cointegration_regression

# Find cointegrated pairs (high correlation)
result = cointegration_regression(df_btc['close'], df_eth['close'])
print(f"Hedge ratio: {result['hedge_ratio']}")
print(f"R-squared: {result['r_squared']}")
```

### 2. Generate Trading Signals
```python
# Identify extreme spreads (buy/sell opportunities)
from strategies.stat_arb import StatArbStrategy

strategy = StatArbStrategy(entry_threshold=2.0)
signals = strategy.generate_signals(z_scores)
```

### 3. Backtest the Strategy
```python
# Simulate trades on historical data
from backtesting.engine import BacktestEngine

backtest = BacktestEngine(initial_capital=100000)
results = backtest.run(prices, signals)
print(f"Sharpe Ratio: {results['metrics']['sharpe_ratio']}")
```

### 4. Run Live (Eventually)
```bash
python main.py live  # Real money (DANGER!)
```

---

## Troubleshooting

### Issue: "Connection refused" (PostgreSQL)

```
Error: could not connect to server: Connection refused
```

**Fix:**
```bash
# Windows: Start PostgreSQL service
'C:\Program Files\PostgreSQL\14\bin\pg_ctl' -D 'C:\Program Files\PostgreSQL\14\data' start

# Or: Check in Services app
services.msc → Find PostgreSQL → Right-click → Start

# Or: Use the built-in utility
net start postgresql-x64-14
```

Then try again:
```bash
python test_setup.py
```

### Issue: "rateLimitExceeded" (Binance API)

```
Error: 429 ("Too Many Requests"
```

**Fix:** Increase the wait time between API calls

Open `main.py` and change:
```python
collector = BinanceCollector(testnet=False, rate_limit_ms=100)
```

To:
```python
collector = BinanceCollector(testnet=False, rate_limit_ms=500)  # Wait 500ms between calls
```

### Issue: "database does not exist" (PostgreSQL)

```
FATAL: database "rafund" does not exist
```

**Fix:** Create the database

```bash
# Login to PostgreSQL
psql -U postgres

# Create database
CREATE DATABASE rafund;

# Connect to it
\c rafund

# Create tables
\i data/schema.sql
```

### Issue: "module not found" (Python)

```
ModuleNotFoundError: No module named 'ccxt'
```

**Fix:** Install dependencies

```bash
pip install -r requirements.txt
```

---

## Key Files Explained

| File | Purpose |
|------|---------|
| `main.py` | Entry point (run with `python main.py collect`) |
| `data/db.py` | PostgreSQL connection & queries |
| `data/collectors/binance_collector.py` | Fetch data from Binance |
| `config/settings.yaml` | System configuration |
| `requirements.txt` | Python dependencies |

---

## Database Schema (Quick Reference)

**prices** table - Raw OHLCV data
```sql
symbol     | timestamp              | open  | high  | low   | close | volume
BTC/USDT   | 2023-04-17 00:00:00   | 28400 | 28800 | 28200 | 28650 | 15000
```

**features** table - Calculated metrics
```sql
symbol_a  | symbol_b  | timestamp | spread | spread_mean | spread_std | z_score | hedge_ratio
BTC/USDT  | ETH/USDT  | 2023-04-17| 3.45   | 3.41        | 0.12       | 0.33    | 0.0598
```

**signals** table - Trading signals
```sql
symbol_a  | symbol_b  | timestamp | signal | position_a | position_b
BTC/USDT  | ETH/USDT  | 2023-04-17| BUY    | 1          | -1
```

---

## Common Questions

**Q: How long does data collection take?**
A: ~30-60 seconds for 4 symbols × 365 days. Depends on internet speed.

**Q: Can I collect more symbols?**
A: Yes! Edit `main.py` and add to the `symbols` list:
```python
symbols = [
    'BTC/USDT',
    'ETH/USDT',
    'SOL/USDT',
    'BNB/USDT',
    'ADA/USDT',    # Add more
    'DOGE/USDT'
]
```

**Q: Can I collect different timeframes?**
A: Yes! Change in `main.py`:
```python
# Instead of '1d' (daily), use:
'1h'   # Hourly
'4h'   # 4-hourly
'15m'  # 15-minute
```

**Q: What if I already have some data?**
A: The system uses `ON CONFLICT ... DO NOTHING`, so duplicates are skipped automatically.

**Q: How do I update with new data?**
A: Run the script daily:
```bash
# Daily update - only fetches new candles since last run
python main.py collect
```

---

## Monitoring Progress

View logs as it runs:

```bash
# Real-time log watching (Linux/Mac)
tail -f logs/ml4t.log

# Real-time log watching (Windows PowerShell)
Get-Content logs/ml4t.log -Wait
```

---

## Success Checklist

- [ ] `python test_setup.py` passes all tests
- [ ] `python main.py collect` completes without errors
- [ ] Database shows 4+ symbols with 365+ records each
- [ ] No error messages in `logs/ml4t.log`
- [ ] Data date range is ~1 year

**If everything is checked, you're ready to proceed to feature engineering!**

---

## Next: Feature Engineering

Once data is collected, calculate trading signals:

```bash
# (Coming soon)
python main.py features
```

This will:
1. Load price data from database
2. Calculate spreads between pairs
3. Compute z-scores
4. Identify entry/exit points

---

**You're all set! Your ML4T system is live and collecting data. 🚀**

Questions? Check `DATA_COLLECTION.md` for detailed documentation.
