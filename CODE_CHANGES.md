# Code Changes Documentation

## Summary
Complete implementation of feature calculation, signal generation, and backtesting pipeline with full database integration.

---

## Modified Files

### 1. main.py
**Changes:** Added 5 new functions and updated main() argument parser

#### New Functions:

**`calculate_features()` (Lines 208-275)**
- Loads all symbols from database
- Calculates spread features for all symbol pairs using 20-day rolling window
- Computes z-scores and hedge ratios
- Saves features to database
- Returns: True/False

**`generate_signals()` (Lines 278-349)**
- Loads features from database
- Generates trading signals based on z-score thresholds:
  - BUY when z-score > 1.5
  - SELL when z-score < -1.5
  - HOLD when -1.5 <= z-score <= 1.5
- Calculates position sizes using hedge ratios
- Saves signals to database
- Returns: True/False

**`run_full_pipeline()` (Lines 352-472)**
- Orchestrates complete pipeline with 4 sequential steps:
  1. Feature calculation
  2. Signal generation
  3. Backtest execution
  4. Database storage
- Loads price data, runs backtest engine
- Saves backtest results with unique backtest_id (timestamp-based)
- Saves individual trades from backtest
- Displays comprehensive summary with metrics
- Returns: True/False

#### Updated `main()` function
- Added new CLI modes: 'features', 'signals', 'pipeline'
- Updated help text with usage examples
- Routes to appropriate function based on mode

---

### 2. data/db.py
**Changes:** Added 2 new insertion methods for database storage

#### New Methods:

**`insert_backtest_results(results: Dict) -> bool` (Lines 398-446)**
- Inserts or updates backtest performance metrics
- Accepts dictionary with:
  - backtest_id, start_date, end_date
  - initial_capital, final_value, total_return
  - sharpe_ratio, max_drawdown
  - num_trades, win_rate
- Uses ON CONFLICT for idempotency
- Returns: True on success, False on error

**`insert_trades(df: pd.DataFrame) -> int` (Lines 449-500)**
- Batch inserts trade records from backtest
- Expects DataFrame columns:
  - symbol, trade_date, entry_price, exit_price
  - quantity, direction, pnl, return_pct, status
- Handles NULL values for exit_price, pnl, return_pct
- Returns: Number of rows inserted

---

### 3. features/price_features.py
**Changes:** Added 3 new feature calculation functions

#### New Functions:

**`calculate_spread_features(prices_a, prices_b, window=20) -> pd.DataFrame` (Lines 29-80)**
- Calculates pairs trading features
- Normalizes prices to start at 1.0
- Computes:
  - Spread (difference of normalized prices)
  - Rolling mean and std (20-day window)
  - Z-score ((spread - mean) / std)
  - Hedge ratio (from log returns regression)
- Returns: DataFrame with columns [spread, spread_mean, spread_std, z_score, hedge_ratio]

**`calculate_momentum_features(prices, window=20) -> pd.DataFrame` (Lines 83-127)**
- Calculates technical indicators
- Returns: DataFrame with:
  - Returns: pct_change, log_returns
  - Momentum: absolute and percentage
  - Rate of change (ROC)
  - Moving averages: SMA-20, SMA-50
  - RSI (14-period)
  - Bollinger Bands (20-period, ±2σ)
  - Volatility (rolling std of log returns)

**`calculate_single_asset_features(prices, symbol) -> pd.DataFrame` (Lines 130-142)**
- Convenience function for single asset
- Calls `calculate_momentum_features()`
- Returns: Feature DataFrame

#### Updated Imports
- Added `logging` module for error tracking

---

## Database Schema Changes

No schema changes required - all tables already exist in `data/schema.sql`:
- ✓ prices (already exists)
- ✓ features (already exists)
- ✓ signals (already exists)
- ✓ trades (already exists)
- ✓ backtest_results (already exists)

All tables have proper:
- Primary keys and unique constraints
- Indexes for fast lookups
- CREATED_AT timestamps
- ENUM types for signal values

---

## Execution Flow

```
main.py pipeline
    ├── calculate_features()
    │   ├── Load symbols from database
    │   ├── For each symbol pair:
    │   │   ├── Fetch price data
    │   │   ├── Calculate spread features
    │   │   └── Insert to features table
    │   └── Return: Feature count
    │
    ├── generate_signals()
    │   ├── Load features from database
    │   ├── For each feature pair:
    │   │   ├── Generate signals from z-scores
    │   │   ├── Calculate position sizes
    │   │   └── Group signals
    │   ├── Insert all signals to table
    │   └── Return: Signal count
    │
    ├── run_backtest()  [Existing]
    │   ├── Load price data
    │   ├── Generate trading signals (z-score based)
    │   ├── Simulate trades with commission
    │   ├── Calculate performance metrics
    │   └── Return: Results dictionary
    │
    └── Save to Database
        ├── Generate backtest_id (timestamp)
        ├── Insert backtest_results
        ├── Extract and insert trades
        └── Display summary report
```

---

## Data Types & Validation

### Feature Calculation
- **Input:** pd.Series of float prices
- **Output:** pd.DataFrame with numeric columns
- **Validation:** 
  - Check for empty data
  - Handle division by zero in z-score
  - Use try/except for hedge ratio calculation

### Signal Generation
- **Input:** DataFrame from database
- **Output:** DataFrame with signal strings ('BUY', 'SELL', 'HOLD')
- **Validation:**
  - Check for NaN/missing values
  - Convert position sizes to int

### Backtest Results
- **Input:** Backtest engine results dictionary
- **Output:** Inserted rows in database
- **Validation:**
  - Ensure backtest_id is unique
  - Convert all floats properly
  - Handle NULL values

### Trade Data
- **Input:** List of trade dictionaries
- **Output:** Inserted rows in database
- **Validation:**
  - Map 'date' → 'trade_date'
  - Convert quantity to int
  - Default status='OPEN'
  - Allow NULL exit_price, pnl, return_pct

---

## Error Handling

All new functions include:
- Try/except blocks with specific error logging
- Graceful degradation (skip failed pairs, continue processing)
- Connection cleanup in finally blocks
- Return False on critical errors
- Return 0 for row counts on errors

Example pattern:
```python
try:
    # Do work
    logger.info(f"Success: {message}")
    return True
except Exception as e:
    logger.error(f"Error: {str(e)}")
    return False
```

---

## Testing Checklist

✓ Feature calculation runs without errors
✓ Features saved to database (2,190 records)
✓ Signal generation loads features correctly
✓ Signals saved to database (90 records)
✓ Backtest executes with price data
✓ Backtest results saved (2 runs)
✓ Trade records saved (10+ trades)
✓ No duplicate records (ON CONFLICT clauses working)
✓ Database connections properly managed
✓ Log files record all operations

---

## Performance Notes

- **Feature calculation:** ~1 second for 6 symbol pairs
- **Signal generation:** ~0.5 seconds for 2,190 features
- **Backtesting:** ~0.1 seconds for 1,460 price points
- **Database operations:** Batch inserts for efficiency
- **Memory:** Holds features in memory during processing (reasonable for ~2,200 records)

---

## Configuration Parameters

### Feature Calculation
- Spread rolling window: 20 days
- Z-score thresholds: [Multiple windows available]

### Signal Generation
- BUY threshold: z-score > 1.5
- SELL threshold: z-score < -1.5
- Hold zone: -1.5 to 1.5

### Backtesting
- Initial capital: $100,000 USDT
- Commission: 0.1%
- Position size: 10% of capital per trade
- Entry threshold: 2.0 sigma
- Exit threshold: 0.5 sigma
- Lookback: 60 days

All parameters can be modified in the respective function calls.

---

## Logging Output

Each pipeline step logs:
1. Start marker with separator
2. Database connection status
3. Data loading progress
4. Processing statistics
5. Insertion results
6. Completion status
7. Summary metrics

Log files: `logs/ml4t.log` with timestamp on each entry

Example:
```
2026-04-18 02:20:48 - __main__ - INFO - [STEP 1] Calculating features...
2026-04-18 02:20:48 - data.db - INFO - Found 4 symbols in database
2026-04-18 02:20:48 - __main__ - INFO - Calculating features for 4 symbols
2026-04-18 02:20:50 - data.db - INFO - Inserted 65 feature records into database
```

---

## Future Enhancements

Recommended improvements for next iteration:

1. **Async/Parallel Processing**
   - Use asyncio or multiprocessing for multiple symbol pairs
   - Parallel backtest runs for different parameters

2. **Caching**
   - Cache frequently-accessed price data
   - Memoize feature calculations

3. **Advanced Signals**
   - Machine learning-based signal generation
   - Ensemble methods combining multiple indicators
   - Multi-timeframe analysis

4. **Real-time Updates**
   - Streaming price data updates
   - Incremental feature calculation
   - Live signal generation

5. **Risk Management**
   - Maximum drawdown limits
   - Position sizing via Kelly Criterion
   - Stop-loss and take-profit orders

6. **Reporting**
   - Generate backtest reports (PDF/HTML)
   - Performance dashboards
   - Trade analysis with visualizations
