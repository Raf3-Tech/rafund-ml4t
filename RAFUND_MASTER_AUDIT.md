# RAFUND ML4T — MASTER AUDIT

**Date:** 2026-06-05
**Synthesizes:** `docs/QUANT_AUDIT_REPORT.md` (research validity) · `RISK_ENGINE_AUDIT.md` (execution/risk) · `ARCHITECTURE_REVIEW.md` (engineering)
**Verdict:** *Promising research-grade scaffolding. Not trustworthy for capital allocation and not production-ready.* The walk-forward skeleton, frozen-baseline rules strategy, permutation test, retry-safe collector, and structured logging are real strengths — but confirmed **data leakage**, **two broken prop-firm controls**, a **single-asset risk blind spot**, and **infrastructure correctness bugs** mean no headline number (e.g. "OOS Sharpe ≥ 1.0") should currently be believed.

---

## Overall Scores

| Domain | Score | One-line verdict |
|---|:--:|---|
| 🧪 **Quant / Research** | **4.8 / 10** (48/100) | Sound chronological harness undermined by 3 leakage paths + an economically inverted ML signal + significance that's off the promotion path. |
| 🛡️ **Risk / Execution** | **4.0 / 10** | Correct only for balanced pair trades. Prop-firm compliance 5/10 (two control bugs); portfolio risk 3/10 (Kelly & vol-targeting absent, controls not wired in). |
| ⚙️ **Engineering** | **4.5 / 10** | Clean package seams undercut by a thread-unsafe pool, connection leaks, swallowed DB errors, a 1,157-LOC `main.py`, and (until now) zero CI. |
| **Composite platform** | **≈ 4.4 / 10** | **Pre-production / research-stage.** Gate to "institutional" ≈ 7/10. |

**Sub-scores (for reference):** Leakage control 30/100 · Walk-forward rigor 55/100 · Feature science 35/100 · Statistical validity 50/100 · Strategy soundness 45/100 · Prop-firm compliance 5/10 · Portfolio risk 3/10 · Test coverage ~46% · CI/CD newly scaffolded.

> **Working-tree note:** several Phase-1 items are already *in progress* in the uncommitted tree — the ML target was realigned to forward spread-change (`models/train.py`), `config/constants.py` (`PERIODS_PER_YEAR`) exists for the √365 fix, and `tests/test_risk_engine_stress.py` encodes the risk bugs as xfails. Verify against the current diff; scores above reflect the audited state.

---

## Top 10 Critical Issues

| # | Domain | Issue | Evidence | Why it's critical |
|---|---|---|---|---|
| 1 | Quant | **Train/validation overlap** — `CandidateModelStrategy.prepare()` is a no-op, so the ML model is **never refit per fold**; a fixed `NOW()-INTERVAL '24 months'` validation window overlaps the 80%-of-history training cut. | `models/validator.py:42-43,204`; `models/train.py:176-180` | The "walk-forward" degenerates into scoring one pre-fit model on partly-seen data → promoted `oos_sharpe` is contaminated. **The single most damaging finding.** |
| 2 | Quant | **Full-sample `hedge_ratio` look-ahead** — computed once over all history, broadcast to every row, persisted, then used as a feature *and* to size the hedge leg. | `features/price_features.py:128-139` | Future information baked into features and positions; also zero-variance → importances meaningless. |
| 3 | Quant | **Look-ahead pair selection** — ADF stationarity gate run over the **entire** spread series to accept/reject pairs. | `features/price_features.py:236`; `main.py` | Classic selection bias: pairs chosen using the test period. |
| 4 | Risk | **Single-asset positions not marked to market** — `_compute_equity` only values `price_a`/`price_b`; single-asset bars carry `price`, so open exposure contributes **$0** to equity. | `engine.py:102-108,384-386` | Every factor-model / single-leg backtest runs with Sharpe, drawdown, daily-loss and the drawdown floor **blind** to the position. |
| 5 | Risk | **Daily-loss limit never compares to its threshold** — mis-parenthesized ternary makes a profitable day halt and the −$200 rule unenforced. | `engine.py:139` | A core prop-firm control is silently dead. |
| 6 | Risk | **Leverage clip clears the daily halt** — `daily_halt = False` inside the notional-clip branch reopens trading after a loss-limit breach. | `engine.py:175-176` | An oversized order resurrects a halted account. |
| 7 | Eng | **Thread-unsafe connection pool** under the `BackgroundScheduler` (retrain/drift/health run concurrently). | `data/db.py:108`; `models/retraining_scheduler.py:43` | `SimpleConnectionPool` free-list corruption can hand one connection to two threads. |
| 8 | Eng | **Connection leaks + swallowed DB errors** — no `try/finally` returns; `delete_*` helpers leak on error; reads return `[]`/`None` on failure. | `data/db.py:673-743,255,794` | ~5 errors exhaust the 5-slot pool → deadlock; a broken DB is indistinguishable from "no data." |
| 9 | Quant | **Significance is off the promotion path; tests assume IID; no multiple-testing correction.** Promotion gates a bare point-estimate Sharpe; the permutation/t-test runs only in the CLI flow. | `models/validator.py:113`; `backtesting/significance.py` | Sweeping pairs × model types against `Sharpe≥1.0` ⇒ high chance of a false "winner"; autocorrelated returns overstate significance. |
| 10 | Quant | **Economically inverted ML signal** — trains on the next-bar z-score *level* and trades `sign(pred)` (momentum) on a series assumed to mean-revert. *(Partially addressed: target realigned to forward spread-change in the uncommitted tree.)* | `models/validator.py:45-60`; `models/train.py:168` | Positions structurally fight the reversion the strategy is premised on. |

---

## Top 10 Quick Wins (high value / low effort)

| # | Fix | Where | Effort |
|---|---|---|---|
| 1 | **Delete the leverage-clip halt reset** (`daily_halt = False`). | `engine.py:176` | 1 line |
| 2 | **Fix the daily-loss parenthesization:** `daily_pnl = equity - daily_start_equity; if daily_pnl <= -capital*pct:` | `engine.py:139` | ~2 lines |
| 3 | **Delete the `db.py` logger override** so DB logs flow through structlog/JSON. | `data/db.py:38` | 1 line |
| 4 | **Remove `'postgres'` password fallbacks** (fail-fast on missing `DB_PASSWORD`). | `main.py:66,562,680`; `run_dashboard.py:95` | trivial |
| 5 | **Annualize with √365** for 24/7 crypto — wire the existing `PERIODS_PER_YEAR` constant. | `significance.py`, `engine.py`, `train.py`, `risk.py`, `monitoring/metrics.py` | small |
| 6 | **Drop the constant, leaky `hedge_ratio` from the model feature set.** | `models/train.py` (`_resolve_feature_names`) | small |
| 7 | **Fractional crypto sizing** — `int(value/price)` truncates to 0 units for a $5k account. | `portfolio/optimizer.py:41` | 1 line |
| 8 | **Forward-fill / skip NaN candles** so a missing bar can't NaN-poison the equity curve. | `engine.py` (price path) | small |
| 9 | **Add `features(symbol_a,timestamp)` + `features(symbol_b,timestamp)` indexes** — the hourly drift + per-retrain validation `OR` predicate is currently unindexed. | new Alembic migration | small |
| 10 | **Wire significance into `validate_for_promotion`** (call `test_strategy_significance` on pooled OOS fold returns; require it alongside the Sharpe/DD gate). | `models/validator.py` | small–med |

*(Bonus, already delivered this session: `.github/workflows/ci.yml` with a 60%→80% ratcheting coverage gate; `try/finally` connection-return cleanup is the next mechanical pass.)*

---

## Roadmap

### Phase 1 — Stabilize  *(make every number trustworthy; nothing ships until this is done)*
**Goal: correctness.** Until these land, no backtest, OOS Sharpe, or promotion decision should be believed.
- **Risk correctness:** F1/F6 mark single-asset positions to market & close at `price`; F2 daily-loss comparison; F3 delete halt reset; F5 NaN-candle guard. Turn the `xfail`s in `tests/test_risk_engine_stress.py` green.
- **Leakage P0s:** make `hedge_ratio` causal (per-fold β, never full-sample) or drop it; enforce train/validation disjointness (refit per fold **or** require `validation_start > train_end`, with a test); select pairs causally (ADF on each fold's train window only); confirm the realigned ML signal trades reversion (unit test).
- **Infra correctness:** `ThreadedConnectionPool` + `try/finally` returns; stop swallowing DB errors (raise typed `DBError`); remove credential fallbacks + implement/remove `${DB_PASSWORD}`; delete the `db.py:38` logger override; collapse schema onto Alembic; add the `features` OR-indexes.
- **CI:** make the lint + coverage gates blocking; add a true train→validate→promote e2e on a temp DB + `file://` MLflow.
- **Exit criteria:** risk stress suite all-green; a written test proves no fold overlaps training; CI blocking; coverage ≥ 60% and climbing.

### Phase 2 — Institutional Research  *(make the science defensible)*
**Goal: statistically honest, regime-aware research.**
- **Statistical hardening:** block/stationary bootstrap (replace IID assumptions), **Deflated/Probabilistic Sharpe** accounting for the number of trials swept, multiple-testing correction (Bonferroni/FDR), Monte-Carlo null distributions.
- **Walk-forward:** add expanding/anchored mode; gate on median + dispersion + `pct_folds_profitable`, not just mean Sharpe.
- **Feature science:** de-duplicate the collinear set (`z_score ≡ f(spread,mean,std)`); add genuinely independent features (funding rate, volume, mean-reversion half-life, spread momentum); reconcile the window-20-vs-60 and three-spread-definition mismatches.
- **Portfolio risk wired in:** integrate `RiskManager`/`PortfolioOptimizer` into the engine; implement **Kelly** (fractional/capped) and **volatility targeting**; portfolio-level gross/net exposure cap; address universe survivorship bias.
- **Execution realism:** next-bar-open fills + latency; volatility-scaled slippage (and thread `bar_volume` through the cost model); short-borrow + perp funding carry for multi-day holds.
- **Exit criteria:** promotion requires significance under autocorrelation-aware tests + DSR; coverage ≥ 80% incl. `db.py` and engine; no leakage tests failing.

### Phase 3 — Live Trading  *(only after Phases 1–2)*
**Goal: safe, observable capital deployment.**
- **Productionize infra:** decompose `main.py` into per-package commands; one config source of truth (`pydantic-settings`) + platform secret store; move MLflow off the deprecated file store to a DB backend; consolidate the DB-connection factory. *(Partially done: the DB-connection/URL factory is consolidated in `config.loader.build_database_url`, used by the app, the SQLAlchemy read engine, and Alembic; `DATABASE_URL` is the single switch. Still open: `main.py` decomposition, `pydantic-settings`, secret store, MLflow backend.)*
- **Live-trading controls:** intra-trade / real-time leverage monitoring (not open-only); kill switches & circuit breakers; order/position reconciliation vs the exchange; data-quality gating before signals; paper→live promotion gate with a soak period.
- **Observability & ops:** alerting on drift-halt / model-block / daily-loss events; dashboards (the new ops dashboard is a start); runbooks; on-call.
- **Exit criteria:** a paper-trading track record reconciles to backtest within tolerance; all prop-firm controls enforced on *both* pair and single-asset paths; SLOs + alerting live.

---

### Provenance & next step
Three independent review streams (quant, risk, engineering) produced the source reports cited at top. **Recommended immediate action:** review the uncommitted working tree (`git diff` — it contains both authored fixes and agent-generated edits), land the Phase-1 quick wins (#1–#5 are one-to-two-line correctness fixes), then commit the audit set (`RAFUND_MASTER_AUDIT.md`, `ARCHITECTURE_REVIEW.md`, `RISK_ENGINE_AUDIT.md`, `docs/QUANT_AUDIT_REPORT.md`, `.github/workflows/ci.yml`) to a branch.

*All findings are evidence-cited to `file:line`; verify against the current diff before acting.*
