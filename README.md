# ML4T - Machine Learning for Trading

A comprehensive Python framework for algorithmic trading using machine learning, backtesting, and data collection from cryptocurrency exchanges.

## Overview

ML4T is a research-grade trading system that combines:
- **Automated data collection** from Binance exchange
- **Machine learning models** for price prediction and signal generation
- **Backtesting engine** with realistic transaction costs and slippage
- **Statistical arbitrage strategies** with fixed-window mean reversion
- **Portfolio analytics** and risk metrics
- **Real-time monitoring** of backtest performance

ML4T is designed for quant researchers, quantitative traders, and trading enthusiasts to develop, test, and analyze trading strategies.

---

## Key Features

### 📊 Data Management
- Automatic price data collection from Binance
- PostgreSQL database for efficient storage and querying
- Data validation and quality checks
- Support for multiple timeframes (1m, 5m, 15m, 1h, 1d)

### 🤖 Machine Learning
- Factor models for market prediction
- Statistical arbitrage strategies
- Feature engineering and technical indicators
- Model training and validation pipelines
- Prediction modules for analysis

### ⚙️ Backtesting
- Realistic order execution simulation
- Transaction costs and slippage modeling
- Performance metrics (Sharpe ratio, max drawdown, win rate, etc.)
- Fixed-window analysis for parameter optimization
- Detailed trade logs and diagnostics

### 💼 Portfolio Analytics
- Risk metrics calculation (Sharpe ratio, max drawdown, Sortino)
- Trade statistics and performance analysis
- Correlation analysis
- P&L attribution

---

## Project Structure

```
ml4t/
├── backtesting/              # Backtesting engine
│   └── engine.py            # Backtest orchestrator with metrics
│
├── data/                    # Data collection and storage
│   ├── db.py               # PostgreSQL database connection
│   ├── schema.sql          # Database schema
│   ├── collectors/         # Exchange data collectors
│   │   ├── binance_collector.py
│   │   └── collect_market_data.py
│   ├── clear_database.py   # Database maintenance
│   └── verify_data.py      # Data validation
│
├── models/                 # Machine learning
│   ├── predict.py         # Prediction module
│   └── train.py           # Model training
│
├── strategies/            # Trading strategies
│   ├── factor_model.py    # Factor model strategy
│   └── stat_arb.py        # Statistical arbitrage
│
├── features/              # Feature engineering
│   ├── price_features.py  # Technical indicators
│   └── factor_models.py   # Factor computations
│
├── portfolio/             # Portfolio analysis
│   ├── optimizer.py      # Portfolio optimization
│   └── risk.py          # Risk analytics
│
├── monitoring/            # Performance tracking
│   └── metrics.py        # Metrics calculation
│
├── config/                # Configuration
│   └── settings.yaml     # Settings file
│
├── tests/                 # Test suite
│   ├── test_setup.py
│   ├── test_metrics.py
│   └── test_window_approaches.py
│
├── dev/                   # Development utilities
│   └── diagnostics/      # Diagnostic tools
│
├── docs/                  # Documentation
│   └── ANALYSIS_ROLLING_WINDOW_PROBLEM.md
│
├── main.py               # Main entry point
├── requirements.txt      # Dependencies
└── logs/                 # Application logs
```

---

## Prop Firm Evaluation Rules

The backtesting engine enforces strict proprietary trading firm evaluation rules aligned with industry standards (similar to Breakout, funded account challenges, etc.).

### Account & Profit Targets

| Rule | Value |
|------|-------|
| **Account Size** | $5,000 |
| **Step 1 Profit Target** | +$250 (5% return) |
| **Step 2 Profit Target** | +$500 (10% return) |
| **Evaluation Fee** | $49.99 |

### Risk Controls

| Rule | Value | Details |
|------|-------|---------|
| **Max Daily Loss** | 4% | Maximum loss per calendar day: $200 |
| **Max Drawdown** | 6% | Equity floor: $4,700 |
| **Max Leverage** | 5x | Notional exposure vs account equity |

### Rules Summary

- **Profit Progression:** After hitting Step 1 (+$250), unlock Step 2 challenge (+$500 total)
- **Daily Stop Loss:** If you lose 4% ($200) in a single day, trading halts for remainder of day
- **Drawdown Floor:** If equity drops below $4,700 (6% drawdown), account is **failed**
- **Leverage Limit:** Maximum notional exposure = 5x account equity
- **Commission:** 0.1% per trade (bid-ask spread)

---

## Installation

### Prerequisites

- **Python 3.8+**
- **PostgreSQL 12+**
- **pip** or **conda**

### Step 1: Clone the Repository

```bash
git clone <repository-url>
cd ml4t
```

### Step 2: Set Up Python Environment

```bash
# Create virtual environment
python -m venv venv

# Activate it
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate
```

### Step 3: Install Dependencies

```bash
pip install -r requirements.txt
```

### Step 4: Set Up PostgreSQL

```bash
# Create database
createdb rafund

# Load schema
psql -U postgres -d rafund -f data/schema.sql
```

### Step 5: Configure Environment Variables

Create a `.env` file in the project root:

```env
# Database Configuration
DB_HOST=localhost
DB_PORT=5432
DB_NAME=rafund
DB_USER=postgres
DB_PASSWORD=your_secure_password
```

> **Security Note:** Never commit `.env` to version control. Add it to `.gitignore`.

### Step 6: Verify Setup

```bash
python test_setup.py
```

You should see:
```
✓ Imports                       PASS
✓ Directory Structure           PASS
✓ Files                         PASS
✓ PostgreSQL                    PASS
✓ Binance API                   PASS

✓ All tests passed! System is ready.
```

---

## Quick Start

### 1. Collect Market Data

```bash
python main.py collect
```

This fetches 4 years of daily OHLCV data for BTC, ETH, SOL, and BNB from Binance and stores it in PostgreSQL.

**Expected output:**
```
====================================
Collecting data for BTC/USDT
====================================
Fetching data...
✓ BTC/USDT: 1460 records inserted
✓ ETH/USDT: 1460 records inserted
✓ SOL/USDT: 1460 records inserted
✓ BNB/USDT: 1460 records inserted

Database now contains:
  Total records: 5840
  Symbols: 4
  Date range: 2020-04-17 to 2024-04-16
```

### 2. Run a Backtest

```bash
python main.py backtest
```

This tests your strategy on historical data, showing:
- Total return
- Sharpe ratio
- Maximum drawdown
- Win rate
- Number of trades
- Detailed trade log

### 3. Analyze Results

Backtest results include:
- Total return and Sharpe ratio
- Maximum drawdown
- Win rate and trade count
- Detailed trade log
- Performance metrics

Use analysis tools to validate strategy performance before considering real trading.

---

## Configuration

### Database Configuration

Edit `SETUP_CONFIG.md` for detailed database setup options.

```python
from data.db import DatabaseConnection

db = DatabaseConnection(
    host='localhost',
    port=5432,
    database='rafund',
    user='postgres',
    password='your_password'
)
```

### Exchange Configuration

Configure collectors in `data/collectors/`:

```python
from data.collectors.binance_collector import BinanceCollector

# Binance data collection
collector = BinanceCollector(testnet=False, rate_limit_ms=100)
```

### Strategy Configuration

Edit `config/settings.yaml` for strategy parameters:

```yaml
strategy:
  name: stat_arb
  symbols: ['BTC/USDT', 'ETH/USDT']
  timeframe: '1d'
  lookback: 60          # Fixed window size
  entry_threshold: 2.0  # Z-score entry threshold
  exit_threshold: 0.5   # Z-score exit threshold
  max_position_pct: 0.10  # Risk control
```

---

## Usage Examples

### Fetch Data Programmatically

```python
from data.db import DatabaseConnection
import pandas as pd

db = DatabaseConnection()

# Get price data
df = db.get_prices('BTC/USDT', start_date='2024-01-01', end_date='2024-04-01')
print(df.head())

db.close_pool()
```

### Train a Model

```python
from models.train import ModelTrainer
from data.db import DatabaseConnection

db = DatabaseConnection()
trainer = ModelTrainer(db)
model = trainer.train('factor_model', 'BTC/USDT')
model.save('models/btc_model.pkl')
```

### Generate Predictions

```python
from models.predict import Predictor
import pickle

with open('models/btc_model.pkl', 'rb') as f:
    model = pickle.load(f)

predictor = Predictor(model)
prediction = predictor.predict(features)
print(f"Price movement: {prediction}")
```

### Run Custom Backtest

```python
from backtesting.engine import BacktestEngine
from strategies.factor_model import FactorModelStrategy

engine = BacktestEngine(
    strategy=FactorModelStrategy(),
    symbols=['BTC/USDT'],
    start_date='2023-01-01',
    end_date='2024-01-01',
    initial_capital=10000
)

results = engine.run()
print(results)
```

---

## API Reference

### Database Module (`data.db`)

**DatabaseConnection**
- `get_prices(symbol, start_date, end_date)` - Fetch OHLCV data
- `insert_prices(df)` - Insert price data
- `get_data_stats()` - Get database statistics
- `test_connection()` - Test PostgreSQL connection

### Backtesting Module (`backtesting.engine`)

**BacktestEngine**
- `run()` - Execute backtest
- `get_metrics()` - Get performance metrics
- `get_trades()` - Get trade history

### Models Module (`models.predict`)

**Predictor**
- `predict(features)` - Generate price predictions
- `predict_batch(features_list)` - Batch predictions

---

## Development

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=.

# Run specific test file
pytest tests/test_backtest.py -v
```

### Code Style

This project follows PEP 8. Format code with:

```bash
black .
flake8 .
```

### Contributing

1. Create a feature branch: `git checkout -b feature/your-feature`
2. Make changes and commit: `git commit -am 'Add feature'`
3. Push to branch: `git push origin feature/your-feature`
4. Open a Pull Request

### Documentation

- See `QUICKSTART.md` for quick setup guide
- See `SETUP_CONFIG.md` for detailed configuration
- See `BUGFIXES_SUMMARY.md` for recent fixes
- See `CODE_CHANGES.md` for code modifications

---

## Troubleshooting

### PostgreSQL Connection Failed

```bash
# Check if PostgreSQL is running
# Windows:
Get-Service postgresql*

# macOS:
brew services list | grep postgres

# Linux:
sudo systemctl status postgresql
```

### Binance API Errors

- Check internet connection
- Verify API rate limits haven't been exceeded
- Ensure Binance servers are accessible
- Check system clock is accurate (Binance requires timestamp sync)

### Data Collection Issues

- Start with fewer symbols
- Increase `rate_limit_ms` to avoid rate limiting
- Check logs in `logs/ml4t.log`

### Backtest Hangs

- Reduce date range for initial testing
- Check database has sufficient data
- Monitor CPU/memory usage

For more help, see the logs directory or check existing documentation files.

---

## Performance

Typical system performance on modern hardware:

- **Data collection:** ~1000 candles/second
- **Backtest:** ~10,000 daily bars/second
- **Model prediction:** ~1000 predictions/second
- **Dashboard:** <100ms response time

---

## Roadmap

- [ ] Add more data sources (Finviz, Alpaca, etc.)
- [ ] Support for options trading
- [ ] Advanced portfolio optimization (CVaR, robust optimization)
- [ ] Live trading module (future)
- [ ] Web dashboard (future)
- [ ] Multi-strategy ensemble
- [ ] Support for other exchanges (HTX, Kraken)

---

## Important Disclaimer

**EDUCATIONAL USE ONLY:** This system is provided for research and educational purposes.

- Backtesting does not guarantee future performance
- Past performance does not predict future results
- Use this system to learn algorithmic trading concepts
- Validate any strategy extensively before considering real trading
- The system has not been tested for live trading

This software is provided "as-is" without warranties.

---

## License

This project is provided for educational and research purposes. Check the LICENSE file for details.

---

## Support

- 📖 **Documentation:** See markdown files in project root
- 🐛 **Issues:** Create an issue on GitHub
- 💬 **Questions:** Check troubleshooting section above
- 📧 **Email:** [Contact information if applicable]

---

## Acknowledgments

Built with:
- Python, pandas, NumPy, scikit-learn
- PostgreSQL for data storage
- CCXT for exchange API abstraction
- FastAPI for web framework
- Plotly for visualization

---

**Last Updated:** May 4, 2026

For the latest updates and documentation, visit the project repository.

---

## Project Status

✅ **Production-Ready Components:**
- Data collection from Binance
- PostgreSQL data storage
- Backtesting engine with accurate metrics
- Statistical arbitrage strategy
- Performance analytics

⏳ **Planned Future Components:**
- Live trading execution
- Interactive dashboard
- Support for additional exchanges
- Advanced optimization algorithms
