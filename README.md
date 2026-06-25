# Raf3nd ML4T

A research-grade, multi-strategy walk-forward backtesting engine for cryptocurrency markets, with paper and live trading on top of the same signal path.

> **Status: Research-grade / pre-production.** Numbers are structurally sound but known limitations (documented below) mean no headline metric should be taken as production-validated. Read the Known Limitations section before drawing any conclusions from backtest results.

---

## What This Is

Raf3nd ML4T is a quantitative trading research platform built around a walk-forward window engine that ranks strategies across three regime tiers (CONSERVATIVE / STANDARD / PERMISSIVE). It supports 13 strategies spanning single-asset, pairs, and perpetual-funding approaches, with ML-assisted regime detection, model lifecycle management, an ops dashboard, and a trade journal. Market data is collected from three exchanges (Binance, Kraken, HTX), and accepted strategies can be run as paper trades or routed to live limit orders.

---

## Module Tree

```
rafund-ml4t/
├── backtesting/
│   ├── engine.py              # Single-asset backtest engine (mark-to-market, risk controls)
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
│   ├── base.py                # BaseStrategy / BasePairsStrategy contracts
│   ├── registry.py            # StrategyRegistry — decorator-based, agents discover strategies without parsing main.py
│   ├── stat_arb.py            # StatArbStrategy (unified signal core) + StatArbPairsStrategy
│   ├── funding_rate_arb.py    # FundingRateArb (8h perpetual carry)
│   ├── smc_breakout.py        # SMC Breakout — structure/BOS + premium-discount zone + engulfing-bar trigger
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
├── trading/
│   ├── position.py            # PositionState — persisted paper/live position + risk-control state
│   ├── paper_trader.py        # Paper trading: one concurrent slot per (strategy × symbol × exchange) with avg_sharpe > 0; day-by-day backfill/replay
│   ├── live_trader.py         # CCXT limit-order execution (opt-in, capped notional, per-exchange API keys)
│   └── alerts.py              # Paper/live trade + risk-control alert dispatch
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
│   ├── db.py                  # PostgreSQL connection + all query methods (insert/get_prices are timeframe-aware)
│   ├── schema.sql             # Reference overview (Alembic is canonical)
│   ├── clear_database.py      # DB maintenance
│   └── collectors/
│       ├── binance_collector.py          # 1m/5m/15m/1h/4h/1d
│       ├── kraken_collector.py           # 1m/5m/15m/1h/4h/1d
│       ├── htx_collector.py              # 1m/5m/15m/1h/4h/1d
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
├── cli/
│   ├── collect.py             # Per-exchange OHLCV + funding-rate collection
│   ├── features.py            # Feature + signal generation
│   ├── backtest.py            # Single backtest, validation, full pipeline, paper/live entry points
│   ├── engine.py               # Walk-forward engine command
│   ├── research.py            # Closed-loop research pipeline + leaderboard command
│   ├── models_cmd.py          # train-classifier / retrain / drift / model status
│   └── db.py                  # DB maintenance commands
│
├── config/
│   ├── settings.yaml          # Strategy + engine parameters
│   ├── constants.py           # PERIODS_PER_YEAR = 365 (24/7 crypto)
│   ├── loader.py              # Config loading
│   ├── logging_config.py      # Logging setup
│   ├── paths.py                # Path resolution
│   └── mlflow_config.py       # MLflow run utilities
│
├── alembic/
│   └── versions/               # 0001 baseline → 0011 (drift reports, engine_results/funding_rates,
│                                # research_decisions, paper trading, exchange column, trade journal,
│                                # manual halt switch, prices.timeframe column)
│
├── research/
│   └── pipeline.py            # Closed-loop research pipeline: propose → engine run → gate → JSONL decision log
│
├── deploy/
│   ├── oracle-bootstrap.sh    # One-shot bootstrap for a fresh Oracle Cloud "Always Free" ARM instance (git clone + Docker Compose)
│   └── update.sh              # Manual update: git pull + rebuild + restart services on the running instance
│
├── tests/                     # 394 passed
├── docs/
│   ├── CANDLESTICK_PATTERNS.md          # Objective candlestick patterns (engulfing bar) feeding SMC Breakout
│   ├── STRATEGY_ENGINE_DESIGN.md
│   ├── QUANT_AUDIT_REPORT.md
│   └── ANALYSIS_ROLLING_WINDOW_PROBLEM.md
├── docker-compose.yml          # trainer (continuous engine/research loop) + dashboard services
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
| 2 | Strategy library (base + 13 strategies) | ✅ complete |
| 3 | Walk-forward window engine (carry, NaN-guard, √365 Sharpe) | ✅ complete |
| 4 | Strategy leaderboard with tier scoring | ✅ complete |
| 5 | Regime classifier | ✅ present (needs 200+ rows of engine results to activate) |
| 6 | Funding data pipeline (collector + table + strategy + engine dispatch) | ✅ complete |
| — | Alembic schema (0001–0011: engine_results, funding_rates, research_decisions, paper trading, exchange column, trade journal, manual halt, `prices.timeframe`) | ✅ complete |
| — | Strategy registry (`strategies/registry.py`) | ✅ complete — `@StrategyRegistry.register` on all 13 strategies; `instantiate_all()` replaces hardcoded list in `run_engine_cmd` |
| — | Portfolio construction | ✅ complete — `risk_parity_weights`, `kelly_fraction`, `multi_strategy_allocate`; `correlation_matrix`, `diversification_ratio`, `concentration_check` |
| — | Closed-loop research pipeline (`research/pipeline.py`) | ✅ complete — `python main.py research` runs propose→engine→gate→JSONL |
| — | Multi-exchange data (Binance, Kraken, HTX) | ✅ complete — `python main.py collect --exchange {binance,kraken,htx,all}` |
| — | Multi-timeframe data storage (1m/5m/15m/1h/4h/1d) | ✅ complete (storage only) — `prices.timeframe` column (Alembic 0011); collectors support `4h`; strategies/engine still consume a single timeframe (`config/settings.yaml`'s global `timeframe: "1d"`) — see Known Gaps |
| — | Paper trading (`trading/paper_trader.py`) | ✅ complete — independent concurrent slot per strategy×symbol×exchange combination with `avg_sharpe > 0` (decoupled from the live-capital `research_decisions.accepted` gate); persists virtual fills to `paper_positions`/`paper_orders`; day-by-day backfill/replay via `--replay-days` or the dashboard "Backfill (paper)" button |
| — | Trade journal (`setup_tag`, `close_reason` on `paper_orders`) | ✅ complete — `/api/trade-journal-summary` joins each CLOSE back to its originating OPEN's `setup_tag` |
| — | Live trading (`trading/live_trader.py`) | ✅ complete — CCXT limit orders only, opt-in via `LIVE_TRADING_ENABLED=1`, notional-capped |
| — | Manual halt switch (dashboard kill switch per paper position) | ✅ complete — `paper_positions.manual_halt` (Alembic 0010) |
| — | CLI (collect, features, engine, leaderboard, train-classifier, research, paper, live, backfill) | ✅ complete |

**Test suite: 394 passed.**

---

## Known Gaps (honest)

1. **Missing tests:** leaderboard scoring/tiers, funding collector pagination, regime classifier gate, and a true end-to-end engine run against a temp DB are not yet covered by tests.
2. **No real engine run yet:** `python main.py engine` has not been run against a populated DB. Synthetic funding Sharpe looked very high on clean sine input — sanity-check on real data is pending.
3. **`BacktestEngine` reuse (optional / architectural):** the window engine has three private P&L loops (single-asset, pairs, funding). Consolidating onto `BacktestEngine` is an architectural cleanup, not a correctness bug — current loops are tested and produce sane results.
4. **Portfolio layer not wired into engine:** `risk_parity_weights`, `kelly_fraction`, and `multi_strategy_allocate` exist as tested library utilities but are not yet called per-bar from the engine. Kelly sizing and VaR limits are computed separately and do not feed back into position sizing during a run (see Phase E in AGENTS.md).
5. **SMC Breakout has no structural stop:** it emits BUY/SELL/HOLD like every other strategy — exits are whatever the engine/paper trader does on opposite-signal or HOLD, not a stop below the order block. Tracked as future work.
6. **Live trading is unexercised against a real exchange account:** the safety gates (opt-in flag, API-key presence, notional cap, limit-only fills) are implemented and unit-tested, but no live order has yet been placed/verified end-to-end on an exchange.
7. **Multi-timeframe data has no consumer yet:** `4h` candles can now be collected and stored (`prices.timeframe`, Alembic 0011) without colliding with `1d` rows, but `strategies/base.py`'s `generate_signals(df, params)` still takes exactly one DataFrame and the walk-forward engine (`backtesting/window_engine.py`) still slices exactly one timeframe per run. A true bias/location/trigger multi-timeframe strategy (e.g. daily bias → 4h location → 1h/15m trigger) needs a new strategy interface and engine support — tracked as future work, not yet started.
8. **Regime classifier output is not a live filter:** `regime_trend`/`regime_direction` (`backtesting/window_engine.py::compute_regime`) are computed per backtest window and shown on the leaderboard, but neither `trading/paper_trader.py`'s candidate selection nor `research/pipeline.py`'s gate check live/recent trend direction before opening a position — there's no "don't go long against the trend" filter yet.

---

## Known Limitations

These are documented research-stage constraints, not hidden bugs:

| Limitation | Where | Impact |
|---|---|---|
| **Single-asset mark-to-market** — open single-asset positions contribute $0 to equity in the *backtest* engine | `backtesting/engine.py` | Drawdown/daily-loss controls in the backtest see only realised P&L. Pairs backtest is correctly marked. Paper/live trading (`trading/position.py`) does mark-to-market correctly. |
| **Same-bar fills (backtest)** — signals and fills resolve on the same bar | `backtesting/engine.py` | Look-ahead bias in execution; next-bar-open fills would be more conservative. Paper/live trading fills against the next available price/limit order, not same-bar. |
| **Flat slippage** — cost model uses a fixed fraction regardless of volume or volatility | `backtesting/costs.py` | Understates costs in thin markets; overstates in deep liquid markets. |
| **`portfolio/` not wired into per-bar engine loop** — `RiskManager` / `PortfolioOptimizer` are library-level utilities (risk-parity allocation, half-Kelly sizing, VaR/CVaR, diversification ratio now present) but not called per-bar from the backtest engine | `portfolio/` | Kelly sizing and VaR limits do not yet feed back into position sizing during an engine run. Phase E in AGENTS.md tracks this. |
| **Regime classifier needs data** — the classifier is non-blocking by design but returns `None` until the `engine_results` table has 200+ rows | `models/regime_classifier.py` | Regime-gated decisions fall back to default tier until enough engine runs accumulate. |
| **Fair Value Gap not used as an entry gate in SMC Breakout** — on this project's daily-bar, 24/7 crypto data a true 3-candle FVG occurs roughly once per 800+ bars | `strategies/smc_breakout.py` | Gating on FVG produced zero trades in walk-forward testing; FVG is defined but not required for entry. Worth revisiting on an intraday timeframe. |

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
python main.py collect                        # OHLCV from Binance (default)
python main.py collect --exchange kraken       # OHLCV from Kraken
python main.py collect --exchange htx          # OHLCV from HTX
python main.py collect --exchange all          # All three exchanges
python main.py collect --funding               # 8h funding rates (Binance)
python main.py features                        # compute features
```

### Deployment

`deploy/oracle-bootstrap.sh` bootstraps a fresh Oracle Cloud "Always Free" ARM instance (Docker + git clone + `docker compose up`) running the `trainer` (continuous research/engine loop) and `dashboard` services from `docker-compose.yml`. `deploy/update.sh` pulls latest and rebuilds in place. GitHub Actions CI is intentionally not used — updates are manual via SSH.

---

## CLI Reference

```bash
python main.py collect [--exchange binance|kraken|htx|all] [--funding]  # Fetch market / funding data
python main.py features                                     # Compute features
python main.py backtest                                     # Single backtest run
python main.py validate                                     # Walk-forward OOS validation
python main.py retrain                                       # Model retraining cycle
python main.py drift                                         # Feature drift check
python main.py engine [--strategy NAME] [--symbol SYM]      # Walk-forward engine: all strategies × all symbols
python main.py leaderboard [--tier TIER]                     # Print ranked strategy leaderboard
python main.py train-classifier                              # Train regime classifier
python main.py research [--strategy NAME] [--symbol SYM]    # Closed-loop research pipeline (propose→backtest→gate)
python main.py research --dry-run                            # Gate only, skip engine run (re-use existing results)
python main.py research --top-n 5 --tier conservative        # Top-5 conservative candidates
python main.py paper [--exchange binance|kraken|htx]          # Run one paper-trading step for every avg_sharpe>0 strategy×symbol slot (default: all exchanges)
python main.py paper --replay-days N [--exchange ...]         # Backfill/replay paper trading day-by-day over the last N days
python main.py backfill --symbol SYM --timeframe TF --from DATE --to DATE   # One-off historical OHLCV backfill (TF: 1m/5m/15m/1h/4h/1d)
python main.py live --exchange binance|kraken|htx             # Live limit-order trading (opt-in, see Known Gaps)
```

---

## Running the Test Suite

```bash
pytest                     # 394 passed
pytest --cov=. -q          # with coverage
```

---

## Architecture Notes

- **Single signal code path (Phase 1):** All call sites for stat-arb signal generation (signals CLI, pairs backtest, engine_eval) delegate to `StatArbStrategy.signals_from_pair_prices` + `to_db_signals`. No duplicate z-score implementations exist.
- **Window engine dispatch:** `window_engine.py` routes by strategy kind: `timeframe=='8h'` → `_run_funding_strategy`; `BasePairsStrategy` → `_run_pairs_strategy`; else single-asset. Each kind has its own mark-to-market loop with the appropriate annualization factor (√365 daily, √1095 for 8h).
- **Position carry:** HOLD signals now maintain the open position and mark it to market each bar. Prior to the Phase-3 fix, every HOLD bar flattened the position, producing artificial 1-bar trades.
- **Multi-exchange data:** `prices`, `paper_positions`, and `paper_orders` all carry an `exchange` column (binance/kraken/htx). Paper and live trading run per-exchange; `python main.py paper` with no `--exchange` cycles all three.
- **Trading methodology pivot:** strategy research is now anchored on Smart Money Concepts (market structure, break of structure, premium/discount zones — see `strategies/smc_breakout.py`) plus the one objective candlestick pattern (engulfing bar, see `docs/CANDLESTICK_PATTERNS.md`), rather than prop-firm challenge framing. Drawdown/daily-loss risk controls remain in the engine and paper/live trader as risk management, not as a pass/fail challenge gate.
- **Trade journal:** `paper_orders.setup_tag` and `close_reason` (Alembic 0009) record why a trade was entered and why it was closed, for post-hoc review.
- **Paper trading is multi-slot:** every strategy×symbol combination with `avg_sharpe > 0` on the leaderboard gets its own independent concurrent `PositionState`, per exchange — paper trading is no longer gated by the live-capital `research_decisions.accepted` flag (live trading still is). `backfill_paper_slot`/`backfill_paper_cycle_all` replay this day-by-day over historical bars to populate the journal without waiting for real time to pass.
- **Live trading is limit-orders-only:** every live order is placed at the signal bar's close and given a timeout to fill; unfilled orders are cancelled rather than chased at a worse price (see `trading/live_trader.py`).
- **Multi-timeframe storage, single-timeframe consumption:** `prices` can hold `1m`/`5m`/`15m`/`1h`/`4h`/`1d` rows per symbol side by side (Alembic 0011), but every strategy, the walk-forward engine, and paper/live trading still operate on one timeframe at a time (`config/settings.yaml`'s global `timeframe`). Storing a second timeframe today requires the manual `python main.py backfill --timeframe 4h ...` path; nothing consumes it yet.
- **Alembic is the schema source of truth.** `data/schema.sql` is a reference overview only; do not load it directly.

---

## Disclaimer

**Research and educational use only.** This system has not been validated for live capital deployment. Past backtest performance does not predict future results. All known limitations are documented above — do not treat any headline metric as a production signal until the audit roadmap items (particularly the mark-to-market and per-bar portfolio wiring) are resolved.

---

**Last Updated:** 2026-06-25
