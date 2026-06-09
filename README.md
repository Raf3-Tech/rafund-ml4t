# Raf3nd ML4T

A research-grade, multi-strategy walk-forward backtesting engine for cryptocurrency markets. Phases 1–6 are functionally complete.

> **Status: Research-grade / pre-production.** Numbers are structurally sound but known limitations (documented below) mean no headline metric should be taken as production-validated. Read the Known Limitations section before drawing any conclusions from backtest results.

---

## What This Is

Raf3nd ML4T is a quantitative trading research platform built around a walk-forward window engine that ranks strategies across three regime tiers (CONSERVATIVE / STANDARD / PERMISSIVE). It supports 12 strategies spanning single-asset, pairs, and perpetual-funding approaches, with ML-assisted regime detection, model lifecycle management, and an ops dashboard.

---

## Module Tree

```
rafund-ml4t/
├── backtesting/
│   ├── engine.py              # Single-asset backtest engine (mark-to-market, prop-firm rules)
│   ├── window_engine.py       # Walk-forward window engine — dispatches all three strategy kinds
│   ├── engine_eval.py         # Pairs evaluation helpers
│   ├── costs.py               # Transaction cost model (flat + slippage)
│   ├── significance.py        # Permutation + t-test significance
│   ├── splitter.py            # Walk-forward fold splitter (expanding + rolling)
│   ├── validation.py          # Single-entry walk-forward validation
│   ├── reporting.py           # Equity-curve reporting
│   ├── walk_forward.py        # Walk-forward validation runner
│   └── persist.py             # Result persistence helpers
│
├── strategies/
│   ├── base.py                # BaseSingleAssetStrategy / BasePairsStrategy contracts
│   ├── registry.py            # StrategyRegistry — decorator-based, agents discover strategies without parsing main.py
│   ├── stat_arb.py            # StatArbStrategy (unified signal core) + StatArbPairsStrategy
│   ├── funding_rate_arb.py    # FundingRateArb (8h perpetual carry)
│   ├── factor_model.py        # ML factor model strategy
│   ├── ema_crossover.py
│   ├── macd.py
│   ├── rsi_extremes.py
│   ├── bollinger_reversion.py
│   ├── donchian_breakout.py
│   ├── atr_volatility_breakout.py
│   ├── keltner_squeeze.py
│   ├── supertrend.py
│   ├── dca.py
│   └── hodl_rebalance.py
│
├── monitoring/
│   ├── leaderboard.py         # Strategy leaderboard scoring (score = sharpe×win_rate×consistency)
│   ├── metrics.py             # Returns / Sharpe / Sortino / Calmar / win-rate
│   ├── dashboard_app.py       # Flask ops-dashboard (model lifecycle)
│   ├── dashboard_data.py      # Dashboard data layer
│   ├── drift_detector.py      # Feature drift detection
│   ├── drift_visualization.py # Plotly drift reports
│   └── run_dashboard.py       # Dashboard launcher
│
├── models/
│   ├── train.py               # ML model training with MLflow integration
│   ├── validator.py           # Model validation + OOS promotion gating
│   ├── regime_classifier.py   # Regime classifier (requires 200+ rows to activate)
│   ├── regime_classifier.pkl  # Trained regime classifier
│   ├── retraining_scheduler.py# Automated retraining + drift scheduling
│   ├── predict.py             # Inference, blocking, latency tracking
│   └── blocker.py             # Stale/drift gate enforcement
│
├── portfolio/
│   ├── risk.py                # VaR, CVaR, max drawdown, correlation, diversification ratio, concentration check
│   └── optimizer.py           # Position sizing, risk-parity weights, half-Kelly fraction, multi-strategy allocation
│   (Note: library-level allocation utilities; not yet wired into the per-bar engine loop — see Known Limitations)
│
├── data/
│   ├── db.py                  # PostgreSQL connection + all query methods
│   ├── schema.sql             # Reference overview (Alembic is canonical)
│   ├── clear_database.py      # DB maintenance
│   └── collectors/
│       ├── binance_collector.py
│       └── binance_funding_collector.py  # 8h funding rate collector
│
├── features/
│   ├── price_features.py      # Technical indicators + spread features
│   └── permutation_entropy.py # Permutation entropy gate
│
├── labels/
│   ├── triple_barrier.py      # Triple-barrier label generation
│   └── barrier_ga.py          # GA-assisted barrier tuning
│
├── config/
│   ├── settings.yaml          # Strategy + engine parameters
│   ├── constants.py           # PERIODS_PER_YEAR = 365 (24/7 crypto)
│   ├── loader.py              # Config loading
│   └── mlflow_config.py       # MLflow run utilities
│
├── alembic/
│   └── versions/
│       ├── 0001_...           # Initial schema
│       ├── 0002_...           # engine_results table
│       └── 0003_add_engine_results_funding_rates.py  # funding_rates table
│
├── research/
│   └── pipeline.py            # Closed-loop research pipeline: propose → engine run → gate → JSONL decision log
│
├── tests/                     # 299 passed, 1 xpassed
├── docs/
│   ├── QUANT_AUDIT_REPORT.md
│   └── STRATEGY_ENGINE_DESIGN.md
├── .github/workflows/ci.yml   # CI with ratcheting coverage gate
├── main.py                    # CLI entry point
├── requirements.txt
└── requirements.lock          # Pinned reproducible environment
```

---

## Phase Completion (per AGENTS.md)

| Phase | Component | Status |
|---|---|---|
| 0 | Regime tiers (CONSERVATIVE/STANDARD/PERMISSIVE) | ✅ complete |
| 1 | Unified signal path | ✅ complete — all call sites route through `StatArbStrategy.signals_from_pair_prices` |
| 2 | Strategy library (base + 12 strategies) | ✅ complete |
| 3 | Walk-forward window engine (carry, NaN-guard, √365 Sharpe) | ✅ complete |
| 4 | Strategy leaderboard with tier scoring | ✅ complete |
| 5 | Regime classifier | ✅ present (needs 200+ rows of engine results to activate) |
| 6 | Funding data pipeline (collector + table + strategy + engine dispatch) | ✅ complete |
| — | Alembic schema (0003: engine_results + funding_rates) | ✅ complete |
| — | Strategy registry (`strategies/registry.py`) | ✅ complete — `@StrategyRegistry.register` on all 12 strategies; `instantiate_all()` replaces hardcoded list in `run_engine_cmd` |
| — | Portfolio construction | ✅ complete — `risk_parity_weights`, `kelly_fraction`, `multi_strategy_allocate`; `correlation_matrix`, `diversification_ratio`, `concentration_check` |
| — | Closed-loop research pipeline (`research/pipeline.py`) | ✅ complete — `python main.py research` runs propose→engine→gate→JSONL |
| — | CLI (engine, leaderboard, train-classifier, collect --funding, research) | ✅ complete |

**Test suite: 299 passed, 1 xpassed.**

---

## Known Gaps (honest)

1. **Missing tests:** leaderboard scoring/tiers, funding collector pagination, regime classifier gate, and a true end-to-end engine run against a temp DB are not yet covered by tests.
2. **No real engine run yet:** `python main.py engine` has not been run against a populated DB. Synthetic funding Sharpe looked very high on clean sine input — sanity-check on real data is pending.
3. **`BacktestEngine` reuse (optional / architectural):** the window engine has three private P&L loops (single-asset, pairs, funding). Consolidating onto `BacktestEngine` is an architectural cleanup, not a correctness bug — current loops are tested and produce sane results.
4. **Portfolio layer not wired into engine:** `risk_parity_weights`, `kelly_fraction`, and `multi_strategy_allocate` exist as tested library utilities but are not yet called per-bar from the engine. Kelly sizing and VaR limits are computed separately and do not feed back into position sizing during a run (see Phase E in AGENTS.md).

---

## Known Limitations

These are documented research-stage constraints, not hidden bugs:

| Limitation | Where | Impact |
|---|---|---|
| **Single-asset mark-to-market** — open single-asset positions contribute $0 to equity | `backtesting/engine.py` | Prop-firm drawdown/daily-loss controls see only realised P&L. Pairs backtest is correctly marked. |
| **Same-bar fills** — signals and fills resolve on the same bar | `backtesting/engine.py` | Look-ahead bias in execution; next-bar-open fills would be more conservative. |
| **Flat slippage** — cost model uses a fixed fraction regardless of volume or volatility | `backtesting/costs.py` | Understates costs in thin markets; overstates in deep liquid markets. |
| **`portfolio/` not wired into per-bar engine loop** — `RiskManager` / `PortfolioOptimizer` are library-level utilities (risk-parity allocation, half-Kelly sizing, VaR/CVaR, diversification ratio now present) but not called per-bar from the backtest engine | `portfolio/` | Kelly sizing and VaR limits do not yet feed back into position sizing during an engine run. Phase E in AGENTS.md tracks this. |
| **Prop-firm control bugs** (audited) — daily-loss parenthesization error, leverage-clip clears the daily halt | `backtesting/engine.py:139,175` | Two prop-firm compliance controls are currently non-functional. |
| **Regime classifier needs data** — the classifier is non-blocking by design but returns `None` until the `engine_results` table has 200+ rows | `models/regime_classifier.py` | Regime-gated decisions fall back to default tier until enough engine runs accumulate. |

See `RAFUND_MASTER_AUDIT.md`, `docs/QUANT_AUDIT_REPORT.md`, and `RISK_ENGINE_AUDIT.md` for the full audit trail.

---

## Installation

### Prerequisites

- Python 3.8+
- PostgreSQL 12+

### Setup

```bash
git clone https://github.com/Raf3-Tech/rafund-ml4t.git
cd rafund-ml4t

python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

pip install -r requirements.lock  # pinned, reproducible
# or: pip install -r requirements.txt  # minimum bounds
```

### Database

```bash
# Set connection (DATABASE_URL takes precedence over DB_* vars)
export DATABASE_URL="postgresql://user:password@localhost:5432/rafund_ml4t"

# Provision schema
alembic upgrade head
```

For a hosted DB (e.g. Supabase), append `?sslmode=require` to the URL and use the Session-pooler connection (port 5432, not the transaction pooler on 6543).

### Collect data

```bash
python main.py collect                     # OHLCV from Binance
python main.py collect --funding           # 8h funding rates
python main.py features                    # compute features
```

---

## CLI Reference

```bash
python main.py collect [--funding]                          # Fetch market / funding data
python main.py features                                     # Compute features
python main.py backtest                                     # Single backtest run
python main.py validate                                     # Walk-forward OOS validation
python main.py retrain                                      # Model retraining cycle
python main.py drift                                        # Feature drift check
python main.py engine [--strategy NAME] [--symbol SYM]     # Walk-forward engine: all strategies × all symbols
python main.py leaderboard [--tier TIER]                    # Print ranked strategy leaderboard
python main.py train-classifier                             # Train regime classifier
python main.py research [--strategy NAME] [--symbol SYM]   # Closed-loop research pipeline (propose→backtest→gate)
python main.py research --dry-run                           # Gate only, skip engine run (re-use existing results)
python main.py research --top-n 5 --tier conservative       # Top-5 conservative candidates
```

---

## Running the Test Suite

```bash
pytest                     # 299 passed, 1 xpassed (expected)
pytest --cov=. -q          # with coverage
```

---

## Architecture Notes

- **Single signal code path (Phase 1):** All three call sites for stat-arb signal generation (signals CLI, pairs backtest, engine_eval) delegate to `StatArbStrategy.signals_from_pair_prices` + `to_db_signals`. No duplicate z-score implementations exist.
- **Window engine dispatch:** `window_engine.py` routes by strategy kind: `timeframe=='8h'` → `_run_funding_strategy`; `BasePairsStrategy` → `_run_pairs_strategy`; else single-asset. Each kind has its own mark-to-market loop with the appropriate annualization factor (√365 daily, √1095 for 8h).
- **Position carry:** HOLD signals now maintain the open position and mark it to market each bar. Prior to the Phase-3 fix, every HOLD bar flattened the position, producing artificial 1-bar trades.
- **Alembic is the schema source of truth.** `data/schema.sql` is a reference overview only; do not load it directly.

---

## Disclaimer

**Research and educational use only.** This system has not been validated for live capital deployment. Past backtest performance does not predict future results. All known limitations are documented above — do not treat any headline metric as a production signal until the audit roadmap items (particularly the mark-to-market and prop-firm control bugs) are resolved.

---

**Last Updated:** 2026-06-09
