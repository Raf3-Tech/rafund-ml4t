# ML4T Reference Guide

## Overview

The ML4T (Machine Learning for Trading) workflow is a systematic approach to quantitative trading that spans from data acquisition through live portfolio execution. This document provides a clear reference for the entire system architecture and key concepts.

---

## The ML4T Workflow

```
DATA SOURCES              → PROCESSING           → MACHINE LEARNING   
├── Market Data           ├── Point-In-Time      ├── Model Design
├── Fundamental Data      │   Adjustments        ├── Parameter Tuning
└── Alternative Data      └── Factor Feature     └── Cross-Validation
                              Engineering
                          ↓
                    EXECUTION & MONITORING
```

### Core Pipeline Stages

1. **Data Sources**: Market, Fundamental, & Alternative data
2. **Point-In-Time Adjustments**: Handle corporate actions, survivorship bias
3. **Factor & Feature Engineering**: Create predictive features
4. **Machine Learning Models**: Design, tune, validate
5. **Execution**: Place orders globally
6. **Live Portfolio**: Track positions in real-time
7. **Monitoring & Evaluation**: Risk management, performance attribution
8. **Predictions**: Generate risk factors, prices, returns, covariance

---

## Project Folder Structure

```
rafund/
├── data/                          # Data pipeline
│   ├── collectors/                # Data collection modules
│   ├── loaders/                   # Data loading utilities
│   └── schema.sql                 # Database schema
│
├── features/                      # Feature engineering
│   ├── price_features.py          # Price-based features
│   └── factor_models.py           # Factor model construction
│
├── models/                        # ML model training & prediction
│   ├── train.py                   # Model training pipeline
│   └── predict.py                 # Model inference
│
├── strategies/                    # Trading strategies
│   ├── stat_arb.py                # Statistical arbitrage
│   └── factor_model.py            # Factor-based strategies
│
├── portfolio/                     # Portfolio management
│   ├── optimizer.py               # Portfolio optimization (asset allocation, sector weights, risk-return profile)
│   └── risk.py                    # Risk management
│
├── execution/                     # Order execution
│   ├── binance.py                 # Binance connector
│   ├── kraken.py                  # Kraken connector
│   └── htx.py                     # HTX connector
│
├── backtesting/                   # Strategy backtesting
│   └── engine.py                  # Backtest engine
│
├── monitoring/                    # Live monitoring
│   └── metrics.py                 # Performance metrics
│
├── dashboard/                     # Web interface
│   └── app.py                     # Flask/FastAPI + React frontend
│
├── config/                        # Configuration
│   └── settings.yaml              # System settings
│
└── main.py                        # Entry point
```

---

## Key Concepts & Formulas

### Statistical Arbitrage - Spread Trading

The core of statistical arbitrage is the **spread** between two correlated assets:

$$\text{spread}_t = \log(P_{A,t}) - \beta \cdot \log(P_{B,t})$$

Where:
- $P_{A,t}$ = Price of Asset A at time $t$
- $P_{B,t}$ = Price of Asset B at time $t$  
- $\beta$ = Hedge ratio (cointegration coefficient)

**Trading Logic:**
- Buy spread when it deviates **below** mean (oversold → profit from mean reversion)
- Sell spread when it deviates **above** mean (overbought → profit from mean reversion)
- Close position when spread reverts to mean

### Z-Score Signal

$$z_t = \frac{\text{spread}_t - \mu}{\sigma}$$

Where:
- $\mu$ = Historical mean of spread
- $\sigma$ = Historical standard deviation

**Trading Rules:**
- If $z_t > 2$: SHORT A, LONG B (spread too high)
- If $z_t < -2$: LONG A, SHORT B (spread too low)
- If $z_t \approx 0$: EXIT (mean reversion achieved)

---

## Data & Portfolio Management

### Live Portfolio Structure
Track real-time positions across symbols:

| Symbol | Position | Entry Price | Current Price |
|--------|----------|-------------|---------------|
| AMZN   | 5000     | $150.23     | $152.45       |
| AAPL   | -2000    | $180.50     | $182.10       |
| UBER   | 1500     | $85.30      | $83.92        |

### Orders Format
Execute trades systematically:

| Symbol | Action | Shares | Limit Price |
|--------|--------|--------|-------------|
| AMZN   | BUY    | 200    | $150.00     |
| AAPL   | SELL   | 50     | $185.00     |
| UBER   | SELL   | 250    | $82.50      |

### Target Portfolio
Define desired end-state positions:

| Symbol | Target Position |
|--------|-----------------|
| AMZN   | 3800            |
| AAPL   | 2300            |
| UBER   | -500            |

---

## Execution & Monitoring

### Risk Management
- Monitor position concentration
- Track leverage ratios
- Implement stop-loss limits
- Calculate Value-at-Risk (VaR)

### Performance Metrics
- Returns (daily, monthly, annual)
- Sharpe Ratio
- Drawdown analysis
- Hit rate & win/loss ratio

### Attribution Analysis
- Identify sources of alpha
- Evaluate factor exposures
- Decompose returns by position

---

## Asset Selection Strategies

### Rule-Based Selection
- Technical indicators (moving averages, RSI, Bollinger Bands)
- Sector rotation logic
- Momentum filters

### Model-Based Selection
- Machine learning predictions
- Factor decomposition
- Statistical signals

### Sizing Strategies
- Fixed position sizing
- Kelly Criterion
- Risk parity allocation

---

## Configuration (settings.yaml)

```yaml
# Database
database:
  type: postgres
  host: localhost
  port: 5432
  name: rafund_db

# API Credentials
exchanges:
  binance:
    api_key: ${BINANCE_KEY}
    api_secret: ${BINANCE_SECRET}
  kraken:
    api_key: ${KRAKEN_KEY}
    api_secret: ${KRAKEN_SECRET}
  htx:
    api_key: ${HTX_KEY}
    api_secret: ${HTX_SECRET}

# Strategy Parameters
strategy:
  spread_lookback: 60        # days
  entry_threshold: 2.0       # standard deviations
  exit_zscore: 0.5           # reversion target
  max_position_size: 10000   # shares
  risk_limit: 0.02           # 2% max loss per trade

# Backtesting
backtest:
  start_date: 2020-01-01
  end_date: 2024-12-31
  initial_capital: 100000
  commission: 0.001          # 0.1%
```

---

## Workflow Summary

1. **Collect & Clean Data** → Validate quality, handle gaps
2. **Feature Engineering** → Create predictive signals
3. **Backtest Strategy** → Verify profitability & robustness
4. **Train ML Models** → Optimize parameters, cross-validate
5. **Paper Trade** → Test live execution without real capital
6. **Live Execution** → Deploy strategy with real capital
7. **Monitor & Adapt** → Track performance, rebalance as needed

---

---
### Data Flow: Concrete Example
1. **DATA LAYER**
   └─ binance.get_ohlcv('BTC/USDT', 'ETH/USDT')
      Returns: [Day1: BTC=$50k ETH=$3k, Day2: BTC=$51k ETH=$2.9k, ...]

2. **FEATURE ENGINEERING**
   └─ factor_models.cointegration_regression(btc_prices, eth_prices)
      Returns: hedge_ratio = 0.06, r_squared = 0.92 (high correlation!)

3. **SIGNAL GENERATION**
   └─ stat_arb.calculate_spread(btc, eth, beta=0.06)
      Returns: spread = [10.82, 10.84, 10.75, ...]
   
   └─ stat_arb.calculate_z_score(spread)
      Returns: z = [-0.5, 0.2, 2.1, ...] ← Z of 2.1 = ENTRY SIGNAL!

4. **PORTFOLIO**
   └─ optimizer.calculate_position_size(signal=1, price=3000, capital=100000)
      Returns: Buy 6.67 ETH (using max 20% of capital = $20k)
               Short 0.4 BTC (matched hedge)

5. **EXECUTION** (backtest mode)
   └─ engine.execute_trade('ETH', 6.67, 2900)
      Cash: $100k → $80.67k (spent $19.33k)
      
   └─ engine.execute_trade('BTC', -0.4, 51000)
      Cash: $80.67k → $80.67k (added $20.4k from short)
      
   Position: Long 6.67 ETH, Short 0.4 BTC

6. **MONITORING** (next day)
   Day 2: BTC=$52k ETH=$2.85k
   - Long ETH: 6.67 × $2.85k = $19.01k (loss of $320)
   - Short BTC: -0.4 × $52k = -$20.8k (loss of $400)
   - Total loss: -$720 from $100k = -0.72%
   
   z_score → 0.3 (back toward mean) → EXIT SIGNAL
   
   Sell ETH, buy back BTC → Realize loss, close trade

7. **BACKTESTING**
   └─ Track: [100k → 99.28k] = -0.72% loss on that trade
      Total portfolio: 100k + (10 trades like this) = Final equity


**Last Updated**: April 2026
