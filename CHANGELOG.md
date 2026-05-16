# CHANGELOG

All notable changes to the ML4T project are documented in this file.

---

## [Current State] - May 4, 2026

### ✅ Completed Tasks

#### Codebase Cleanup
- Removed duplicate backtesting engine (`engine.py` - replaced by `engine_v2.py`)
- Removed redundant data collection script (`collect_5year_data.py`)
- Removed duplicate data verification script (root `verify_data.py`)
- Deleted stub execution modules (Binance, Kraken, HTX connectors)
- Deleted non-functional dashboard skeleton
- Removed empty `data/loaders/` directory
- Cleaned up temporary files (`test_output.txt`, `data_collection_run.log`)

#### Project Reorganization
- Created `tests/` directory structure
- Created `dev/diagnostics/` for development-only scripts
- Created `docs/` for documentation files
- Moved `debug_trade_lifecycle.py` to `dev/diagnostics/`
- Moved `analyze_rolling_window_problem.py` to `dev/diagnostics/`
- Moved test files to `tests/` directory
- Renamed `test_fixed_window.py` to `test_window_approaches.py`

#### Documentation Updates
- Updated README.md to reflect actual implementation status
- Removed false claims about live trading, dashboard, multiple exchanges
- Added "Project Status" section with honest feature list
- Changed disclaimer from "Risk" to "Educational Use" focus
- Created `docs/ANALYSIS_ROLLING_WINDOW_PROBLEM.md` with comprehensive analysis
- Consolidated roadmap with clear future vs current state

#### Repository Structure
```
Before:
- 6 redundant/duplicate files
- 11 markdown documentation files (overlapping content)
- 5 stub/non-functional modules
- Development scripts in root directory
- Test files scattered in root

After:
- Clean, focused file structure
- Consolidated documentation
- Development files properly organized
- Test suite in dedicated directory
- Only production-ready code in main modules
```

---

## Critical Bug Fixes

### Rolling Window Problem (Statistical Arbitrage)
**Status:** ✅ FIXED in `engine_v2.py`

**Problem:**
- Original implementation used rolling windows that shifted every day
- Z-score could decrease due to window drift, not actual mean reversion
- Strategy was "chasing windows" rather than trading mean reversion
- False entry/exit signals resulted in losses

**Solution:**
- Implemented fixed-window approach in `BacktestEngineV2`
- Baseline statistics calculated once from initial data
- All z-scores reference the same fixed baseline
- True mean reversion signals, not window drift artifacts

**Implementation Details:**
- Default behavior: `use_fixed_window=True`
- Training window: First `lookback` days establish baseline
- Testing window: Remaining data uses fixed baseline
- See `backtesting/engine.py` for implementation

**Validation:**
- See `tests/test_window_approaches.py` for comparison
- See `docs/ANALYSIS_ROLLING_WINDOW_PROBLEM.md` for detailed analysis

---

### Sharpe Ratio Calculation
**Status:** ✅ FIXED in `engine_v2.py`

**Problem:**
- Original calculation used sparse equity curve data
- Sharpe ratio was not properly annualized
- Daily returns miscalculated from non-daily price points

**Solution:**
- Properly calculate daily returns from daily equity values
- Correctly annualize Sharpe ratio: sqrt(252) * mean_return / std_return
- Validate data availability before calculation

**Before:**
```python
# Wrong: used sparse equity data
sharpe_ratio = (annual_return - risk_free_rate) / volatility
```

**After:**
```python
# Correct: daily returns from daily equity
daily_returns = equity_curve.pct_change()
sharpe_ratio = sqrt(252) * daily_returns.mean() / daily_returns.std()
```

---

### Position Sizing and Risk Controls
**Status:** ✅ ADDED in `engine_v2.py`

**Improvements:**
- Added `max_position_pct` parameter for risk control
- Added `stop_loss_pct` parameter for protection
- Position size dynamically calculated based on capital
- Improved trade execution logic

**Parameters:**
```python
BacktestEngineV2(
    max_position_pct=0.10,    # Max 10% of capital per trade
    stop_loss_pct=0.05,       # 5% stop loss
    use_fixed_window=True     # Fixed window approach
)
```

---

## Data Pipeline Changes

### Data Collection Improvements
**Status:** ✅ UPDATED

**Changes:**
- Removed duplicate `collect_5year_data.py`
- Consolidated into single `data/collectors/collect_market_data.py`
- Parameterized date ranges via environment variables
- Improved error handling and logging

**Configuration:**
- Start date: Configurable via parameter
- End date: Configurable via parameter
- Symbols: Defined in strategy configuration
- Rate limiting: Adjustable via `rate_limit_ms` parameter

---

### Database Schema
**Status:** ✅ STABLE

**Tables:**
- `prices` - OHLCV data with timestamps
- Schema defined in `data/schema.sql`
- PostgreSQL connection via `data/db.py`

---

## Testing Infrastructure

### Test Suite Organization
**Status:** ✅ RESTRUCTURED

**Location:** `tests/` directory

**Available Tests:**
- `test_setup.py` - Verify system dependencies and configuration
- `test_metrics.py` - Test metrics calculations
- `test_window_approaches.py` - Compare fixed vs rolling windows

**Running Tests:**
```bash
# All tests
pytest tests/

# Specific test
pytest tests/test_metrics.py -v

# With coverage
pytest tests/ --cov=.
```

---

## Known Limitations

### Not Implemented
1. **Live Trading** - Execution connectors are not built
2. **Dashboard** - Web UI not implemented
3. **Multiple Exchanges** - Only Binance data collection works
4. **Portfolio Optimization** - Advanced optimization algorithms not implemented
5. **Kalman Filtering** - Not implemented for mean reversion
6. **Options Trading** - Not supported

### Planned for Future
- Live execution module
- Web dashboard (FastAPI)
- Support for HTX and Kraken exchanges
- Advanced portfolio optimization (CVaR, robust optimization)
- Real-time WebSocket data feeds
- Multi-strategy ensemble

---

## Version History

### Engine Versions

**BacktestEngine (Original)** - Version 1.0
- Basic backtesting functionality
- Rolling window approach
- Sharpe ratio calculation issues
- ❌ DEPRECATED - Use engine_v2

**BacktestEngineV2 (Current)** - Version 2.0
- Fixed rolling window problem
- Correct Sharpe ratio calculation
- Improved risk controls
- ✅ CURRENT - Default implementation

---

## Code Quality Improvements

### Recent Improvements
- ✅ Removed duplicate code
- ✅ Reorganized project structure
- ✅ Consolidated documentation
- ✅ Improved test organization
- ⏳ Type hints (in progress)
- ⏳ Comprehensive docstrings (in progress)
- ⏳ Enhanced error handling (in progress)

### Standards Applied
- Python 3.8+ compatibility
- PEP 8 code style
- Google-style docstrings (being added)
- Type hints for all function signatures (being added)

---

## Migration Guide

### For Existing Users

#### If You Were Using `backtesting/engine.py`:
**Old:**
```python
from backtesting.engine import BacktestEngine
```

**New:**
```python
from backtesting.engine import BacktestEngine  # Now points to v2
```

The module was renamed, but imports remain the same. No code changes needed.

#### If You Were Using Rolling Windows:
**Old Configuration:**
```python
engine = BacktestEngine(lookback=60, use_fixed_window=False)  # Not available
```

**New Configuration:**
```python
engine = BacktestEngine(lookback=60, use_fixed_window=True)  # Default
```

Fixed window is now the default and recommended approach.

#### If You Were Collecting Data:
**Old:**
```bash
python data/collect_5year_data.py
```

**New:**
```bash
python main.py collect
# OR
python data/collectors/collect_market_data.py
```

---

## Dependencies

### Core Libraries
- Python 3.8+
- pandas 2.2.0
- numpy 1.24.3
- PostgreSQL 12+
- psycopg2-binary 2.9.9
- ccxt 4.0.95

### Optional
- scikit-learn 1.3.2 (for ML models)
- pytest 7.4.3 (for testing)
- pytest-cov 4.1.0 (for coverage)

See `requirements.txt` for complete list.

---

## Breaking Changes

### Version 2.0 Changes
- Removed `backtesting/engine.py` - Use `engine_v2.py` instead
- Removed `data/collect_5year_data.py` - Use `collect_market_data.py` instead
- Removed `verify_data.py` (root) - Use `data/verify_data.py` instead
- Removed execution modules - Not implemented
- Removed dashboard module - Not implemented

### Migration Steps
1. Update imports if necessary (should be automatic)
2. Update test files if running from root (move to `tests/`)
3. Update data collection calls to use `main.py collect`

---

## Issue Resolution

### Fixed Issues
- ✅ Rolling window z-score drift problem
- ✅ Sharpe ratio calculation errors
- ✅ Duplicate code in data collection
- ✅ Test file organization
- ✅ Documentation fragmentation
- ✅ False promises about features not implemented

### Known Issues
- ⏳ Some modules need type hints
- ⏳ Error messages could be more specific
- ⏳ Parameter validation could be more comprehensive

---

## Acknowledgments

This changelog documents the evolution of ML4T from a working prototype to a clean, maintainable research framework.

Key improvements:
- Honest feature documentation
- Clean project structure
- Fixed critical statistical bugs
- Proper test organization
- Focused scope (research, not production trading)

---

**For detailed analysis of specific fixes, see:**
- `docs/ANALYSIS_ROLLING_WINDOW_PROBLEM.md` - Rolling window bug explanation
- `dev/diagnostics/analyze_rolling_window_problem.py` - Original analysis code
- `tests/test_window_approaches.py` - Comparison testing

---

**Last Updated:** May 4, 2026

