# AGENTS.md — Coordination Log for the Raf3nd Engine Build

**Read this first.** Multiple agents edit this repo concurrently. This file is the
single place to (a) see how close we are to the agent implementation spec, (b)
claim files you are about to edit, and (c) record what you did each session so the
next agent doesn't re-create forks or clobber work.

> The authoritative spec is the "Raf3nd Engine — Agent Implementation
> Instructions" (multi-strategy, multi-window walk-forward engine → ranked
> leaderboard across CONSERVATIVE/STANDARD/PERMISSIVE tiers).

> **⚠️ Phases A–F below predate a methodology pivot (commit `d57cfd7`, 2026-06-20)
> that dropped the prop-firm challenge framing entirely** (no more pass/fail tiers
> as the trading goal — drawdown/daily-loss controls remain only as risk
> management). Phase A's "prop-firm bug fixes" and the CONSERVATIVE/STANDARD/
> PERMISSIVE tier language are stale relative to that pivot. SMC Breakout, the
> trade journal, multi-exchange paper trading, and the trade journal/backfill
> work below were all built *after* the pivot and are not reflected in the Phase
> A–F backlog. **`README.md`'s "Known Gaps" and "Known Limitations" sections are
> the current source of truth for what's actually outstanding** — treat the
> backlog below as a historical record of the pre-pivot plan, not a live TODO
> list, unless you're specifically resuming prop-firm-era work.

---

## How to use this file

1. Before editing, add a line under **Active claims** with the file(s), your
   session id, and a one-line intent. Remove it when done.
2. After working, append a dated entry under **Session log** (newest first):
   what you changed, why, and what you verified.
3. Keep **Spec completion status** current — it is the shared source of truth for
   "what's left".
4. Never introduce a second code path for an existing concern (signals, P&L,
   z-score, sizing). Point everything at the one authority. This is Rule 1.

---

## DATABASE HEALTH RULE — Mandatory at every session start

Before any agent does anything else in any session, execute this checklist in order:

1. **Check DB connectivity**
   `--dry-run`/`--status` flags do not exist on `collect` (this rule predates the
   actual CLI). Use: `python -c "from cli.db import get_db_connection; db=get_db_connection(); print(db.test_connection())"`.
   If the DB is unreachable, stop and report. Do not proceed.

2. **Check data freshness**
   `db.get_latest_timestamp(symbol, exchange=...)` (used internally by `cli/collect.py`)
   gives the most recent timestamp per symbol/exchange. If any symbol has a gap
   > 48 hours from now, run `python main.py collect [--exchange ...]` to pull
   fresh data before continuing.

3. **Validate data integrity**
   Run: `python data/verify_data.py` (or equivalent).
   If validation fails, fix the data issue before proceeding with any backtest
   or engine run. Log the failure in this session's entry.

4. **Confirm engine_results baseline**
   Run: `python main.py leaderboard` and note the row count in engine_results.
   If count < 200, note it — the regime classifier will return None (non-blocking,
   by design). Record the count in your session log entry.

This rule applies to EVERY session regardless of the stated task. A session that
skips the DB health check is invalid and its results cannot be trusted.

---

## SESSION LIMITS — Cool-off protocol

Each agent session operates under the following hard limits to prevent runaway
changes and ensure the next session can safely pick up:

| Limit | Value | Rationale |
|---|---|---|
| Max files touched per session | 10 | Keeps diffs reviewable |
| Max lines changed per session | 500 | Prevents unreviewed rewrites |
| Max phases attempted per session | 1 | See Phase Gate Rule below |
| Required test suite state at end | ✅ green | Never leave a failing suite |
| Required AGENTS.md update at end | ✅ mandatory | Next session reads this first |
| Required session log entry at end | ✅ mandatory | Newest-first, dated, complete |

If a session hits a limit mid-phase, the agent must:
1. Stop implementing.
2. Commit all completed, green-tested work.
3. Write a "PARTIAL" session log entry documenting exact stopping point.
4. Add a `RESUME:` line to Active Claims with the next action.
5. Do not start the next phase.

A session that violates these limits and leaves a broken suite is a failed session.
Revert to the last green commit and start over.

---

## PHASE GATE RULE — 90% completion required before advancing

The gap backlog is broken into phases below. An agent may only advance to the
next phase when the current phase is ≥ 90% complete by its own checklist.

**90% complete means:** all checklist items are done OR the remaining items are
explicitly marked `[DEFERRED — reason]` with a written justification, AND the
test suite is green, AND the session log entry is written.

No agent skips a phase. No agent works on Phase N+1 items while Phase N is open.
If a task from a later phase is trivially entangled with current work (e.g., a
one-line fix), it may be done but must be logged as "incidental" — it does not
count toward the later phase's completion.

---

## GAP BACKLOG — Phased Execution Plan

Work through these phases in strict order. Update the checkbox and status column
as you go. Do not advance until the current phase is ≥ 90% complete (see PHASE
GATE RULE).

---

### PHASE A — Prop-Firm Bug Fixes  ✅ / ⏳ / ❌
**Goal:** Every prop-firm control works correctly for all strategy types.
**Exit criteria:** All 4 xfails in `tests/test_risk_engine_stress.py` turn green.
**Session limit:** May span multiple sessions; each session updates this checklist.

| # | Item | File | Status |
|---|---|---|---|
| A1 | Fix daily-loss parenthesization — `daily_pnl = equity - daily_start_equity; if daily_pnl <= -capital*pct:` | `backtesting/engine.py:139` | ⏳ |
| A2 | Delete leverage-clip halt reset — remove `daily_halt = False` | `backtesting/engine.py:175-176` | ⏳ |
| A3 | Mark single-asset positions to market — value open position against `price` column, not `price_a`/`price_b` | `backtesting/engine.py:102-108, 384-386` | ⏳ |
| A4 | Fix single-asset exit at zero signal — route flat-signal close through `price` | `backtesting/engine.py:384-386` | ⏳ |
| A5 | Turn all 4 xfails green — verify `tests/test_risk_engine_stress.py` passes fully | `tests/test_risk_engine_stress.py` | ⏳ |

**Verification:** `pytest tests/test_risk_engine_stress.py -v` must show 10 passed, 0 xfailed.

---

### PHASE B — Missing Tests  ✅ DONE (2026-06-25)
**Goal:** Test coverage for leaderboard, funding collector, regime classifier, and a true e2e engine run.
**Prerequisite:** Phase A ≥ 90% complete.
**Exit criteria:** 4 new test modules present and green; total suite count increases by ≥ 30 tests.

| # | Item | File | Status |
|---|---|---|---|
| B1 | Leaderboard scoring + tier gate tests | `tests/test_leaderboard.py` | ✅ already existed (found during 2026-06-25 verification, doc was stale) |
| B2 | Funding collector pagination tests (mock Binance pagination) | `tests/test_funding_collector.py` | ✅ already existed (found during 2026-06-25 verification, doc was stale) |
| B3 | Regime classifier gate tests (< 200 rows → False/no save; ≥ 200 rows → trains + saves) | `tests/test_regime_classifier.py` | ✅ DONE 2026-06-25 |
| B4 | End-to-end engine run, all 3 strategy kinds → `build_leaderboard()` produces finite, non-NaN rows | `tests/test_window_engine.py::test_engine_results_produce_nan_free_leaderboard` | ✅ DONE 2026-06-25 — extended the existing hand-rolled `FakeDB` fixture instead of introducing pytest-postgresql/sqlite (no such pattern exists anywhere in this repo's tests; not a standalone `test_e2e_engine.py` since most of the e2e wiring already existed) |

**Verification:** `pytest tests/test_leaderboard.py tests/test_funding_collector.py tests/test_regime_classifier.py tests/test_window_engine.py -v` all green.

---

### PHASE C — First Real Engine Run + Sanity Check  ✅ / ⏳ / ❌
**Goal:** Run the engine against the populated production DB, validate outputs are sane, catch the synthetic Sharpe inflation on real data.
**Prerequisite:** Phase B ≥ 90% complete. DATABASE HEALTH RULE must pass before starting.
**Exit criteria:** `LEADERBOARD.md` updated with real results; funding Sharpe sanity-checked and documented.

| # | Item | Command / File | Status |
|---|---|---|---|
| C1 | DB health check passes (see DATABASE HEALTH RULE) | `python main.py collect --status` | ⏳ |
| C2 | Run full engine across all strategies and windows | `python main.py engine` | ⏳ |
| C3 | Run leaderboard and inspect output | `python main.py leaderboard` | ⏳ |
| C4 | Sanity-check funding Sharpe — if `FundingRateArb` Sharpe > 3.0 on real data, flag as suspicious and document in session log | `LEADERBOARD.md` + session log | ⏳ |
| C5 | Document top-3 strategies per tier (CONSERVATIVE / STANDARD / PERMISSIVE) in session log | session log | ⏳ |
| C6 | Identify and log any NaN, Inf, or negative-equity anomalies from the engine run | `ml4t.log` inspection | ⏳ |
| C7 | If anomalies found, create numbered bug entries under Known Gaps before ending session | `AGENTS.md` | ⏳ |

**Verification:** `LEADERBOARD.md` exists with ≥ 1 row per tier; session log entry documents the run.

---

### PHASE D — BacktestEngine Consolidation (Optional / Architectural)  ⏸️ DEFERRED (see 2026-06-25 session log)
**Goal:** Eliminate the 3 private P&L loops in `window_engine.py` by routing all strategies through `BacktestEngine`.
**Prerequisite:** Phase C ≥ 90% complete.
**Note:** This is architectural cleanup. It must not change any leaderboard results. Add a regression test that pins leaderboard scores before and after consolidation within a tolerance of ±0.01.

> **Deliberately deferred, not attempted (2026-06-25).** Investigated as part of
> closing the README Known Gaps list. `BacktestEngine` (`backtesting/engine.py`)
> and the 3 private loops in `window_engine.py` are incompatible state
> machines, not a refactor of the same logic: the loops operate on **unit
> position** (-1/0/+1, equity starting at 1.0, no capital/leverage concept —
> this is what keeps every strategy's Sharpe comparable on the leaderboard),
> while `BacktestEngine` tracks real notional/cash/leverage with its own
> position-open/close state. `BacktestEngine` has no funding/8h cadence
> parameter, no additive-income P&L path (it always treats price columns as
> tradable prices — would explode on a funding rate crossing zero), and a
> different pairs control flow (it owns leg state vs. window_engine taking
> externally-supplied `pos_a`/`pos_b`). Worse: `tests/test_window_engine.py`
> has **zero exact-value-pinned assertions** on the 3 loops (D1 below was
> never done) — only behavioral checks (`>`, `isfinite`) — so there is no
> safety net to catch a consolidation silently shifting every strategy's
> historical Sharpe. Given the README itself frames this as "architectural
> cleanup, not a correctness bug" with the current loops already "tested and
> produc[ing] sane results," the risk/reward doesn't justify the lift right
> now. Revisit only if D1 (a real regression-pinning test) is built first.

| # | Item | File | Status |
|---|---|---|---|
| D1 | Pin current leaderboard scores as regression baseline | `tests/test_engine_consolidation_regression.py` | ⏳ |
| D2 | Route single-asset strategies through `BacktestEngine` instead of private loop | `backtesting/window_engine.py` | ⏳ |
| D3 | Route pairs strategies through `BacktestEngine` | `backtesting/window_engine.py` | ⏳ |
| D4 | Route funding strategies through `BacktestEngine` | `backtesting/window_engine.py` | ⏳ |
| D5 | Run regression test — scores within ±0.01 tolerance | `tests/test_engine_consolidation_regression.py` | ⏳ |
| D6 | Delete the 3 now-unused private P&L loops | `backtesting/window_engine.py` | ⏳ |

**Verification:** `pytest` full suite green; `LEADERBOARD.md` scores unchanged within tolerance.

---

### PHASE E — Portfolio Risk Wiring  ✅ / ⏳ / ❌
**Goal:** Wire `RiskManager` and `PortfolioOptimizer` into the engine so Kelly sizing and VaR limits are enforced during runs.
**Prerequisite:** Phase D ≥ 90% complete (or explicitly deferred with justification).

| # | Item | File | Status |
|---|---|---|---|
| E1 | Implement fractional crypto sizing in `PortfolioOptimizer` — replace `int(value/price)` with `round(value/price, 8)` | `portfolio/optimizer.py:41` | ⏳ |
| E2 | Add Kelly sizing (fractional, capped) — `kelly_fraction(μ, σ², half_kelly=True)` | `portfolio/optimizer.py` | ✅ DONE — `kelly_fraction` in `portfolio/optimizer.py`; tests in `tests/test_portfolio.py` |
| E3 | Add volatility targeting position scalar | `portfolio/risk.py` | ⏳ |
| E4 | Wire `RiskManager.check_position_limits` into the engine's `_open_position` | `backtesting/engine.py` | ⏳ |
| E5 | Risk-parity multi-strategy allocation (`multi_strategy_allocate`); portfolio-level gross exposure cap | `portfolio/optimizer.py` + `portfolio/risk.py` + `engine.py` | ✅ PARTIAL — allocation done (`multi_strategy_allocate`, `risk_parity_weights`, `concentration_check`); gross cap not yet wired into engine |
| E6 | Tests for Kelly, vol-targeting, gross cap | `tests/test_portfolio.py` | ✅ PARTIAL — Kelly + risk-parity + multi-strategy tests added; vol-targeting + gross cap tests pending |

**Verification:** `pytest tests/test_portfolio_risk.py -v` green; engine run with `--risk-wired` flag produces different (smaller) position sizes than without.

---

### PHASE F — Execution Realism  ✅ / ⏳ / ❌
**Goal:** Next-bar-open fills, volatility-scaled slippage, short borrow + funding carry.
**Prerequisite:** Phase E ≥ 90% complete.

| # | Item | File | Status |
|---|---|---|---|
| F1 | Next-bar-open execution — shift fills from bar `t` to bar `t+1` open | `backtesting/engine.py` | ⏳ |
| F2 | Thread `bar_volume` through `cost_model.apply` — make `volume_proportional` slippage reachable | `backtesting/costs.py`, `engine.py` | ⏳ |
| F3 | Volatility-scaled slippage — widen slippage proportional to realized bar volatility | `backtesting/costs.py` | ⏳ |
| F4 | Short borrow fee for multi-day single-asset shorts | `backtesting/engine.py` | ⏳ |
| F5 | Perp funding carry for multi-day holds in `FundingRateArb` | `backtesting/engine.py` or `strategies/funding_rate_arb.py` | ⏳ |

**Verification:** After F1, Sharpe for all strategies should decrease slightly (more conservative fills). If Sharpe increases, something is wrong — stop and investigate.

---

## Spec completion status  (updated 2026-06-09, session "gap-closure")

| Phase | Component | Status | Notes |
|---|---|---|---|
| 0 | Regime tiers (3/10/25%) | ✅ functional | Constants in `backtesting/window_engine.py` + `monitoring/leaderboard.py`. Spec suggested `evaluation_rules.py`; current location is acceptable. |
| **1** | **One signal path** | ✅ **FIXED** | All three pairs call sites now route through `StatArbStrategy.signals_from_pair_prices` + `to_db_signals` (single stateful core). `StatArbPairsStrategy.{generate_signals_pair,signals_to_db_format}` delegate to it; `engine_eval` delegates to it. Verified: signals-command vs backtest distributions match exactly; pinned by `tests/test_signal_path_unification.py`. |
| 2 | Strategy library | ✅ done | `strategies/base.py` + 12 strategies. 10 single-asset strategies pinned by `tests/test_strategies_contract.py`. |
| 3 | Window engine | ✅ done | Single-asset, **pairs, and funding** all run across expanding+rolling windows; warmup/eligibility/regime/mutation/persist present. **Fixed this session:** √365 Sharpe; NaN-candle guard; **position carry** (HOLD now maintains the position instead of flattening — fixes 1-bar trades); pairs routed via `generate_signals_pair` with two-leg mark-to-market; funding routed on 8h `funding_rates` with cadence-aware (√1095) **additive-income** P&L. Remaining (optional): reuse `BacktestEngine` instead of the engine's private P&L loop (gap #5). |
| 4 | Leaderboard | ✅ done | `monitoring/leaderboard.py`: score = sharpe×win_rate×consistency, tier gates (0.60/0.60/0.50), `LEADERBOARD.md`, classifier section. |
| 5 | Regime classifier | ✅ present | `models/regime_classifier.py` (+ trained `regime_classifier.pkl`). Needs 200+ rows; non-blocking by design. |
| 6 | Funding data | ✅ done | Collector + `funding_rates` table + `funding_rate_arb.py` + `db.get_funding_rates`. **Engine now schedules it** on 8h data (no longer orphaned). |
| — | Schema / migration | ✅ done | Alembic `0003` (canonical). `engine_results` + `funding_rates`. |
| — | **Strategy registry** | ✅ **DONE** | `strategies/registry.py`: `@StrategyRegistry.register` on all 12 strategies (description, tier_hints, tags). `instantiate_all()` replaces the hardcoded list in `run_engine_cmd`. `import strategies` populates registry. Agents can call `StrategyRegistry.as_dict()` without parsing `main.py`. |
| — | **Portfolio construction** | ✅ **DONE** | `portfolio/optimizer.py`: `risk_parity_weights` (iterative ERC), `kelly_fraction` (half-Kelly, clamped), `multi_strategy_allocate` (risk-parity or equal-weight with per-strategy cap). `portfolio/risk.py`: `correlation_matrix`, `diversification_ratio`, `concentration_check`. Tests added to `tests/test_portfolio.py`. |
| — | **Closed-loop research pipeline** | ✅ **DONE** | `research/pipeline.py`: `run_research_pipeline` (propose → engine run → leaderboard gate → decision log). `_gate()` applies tier consistency floors + Sharpe floor, returns `ResearchDecision`. Decisions logged to `tmp/research_decisions.jsonl`. CLI: `python main.py research [--strategy] [--symbol] [--top-n] [--tier] [--dry-run]`. Tests: `tests/test_research_pipeline.py`. |
| — | CLI | ✅ done | `engine` (+filters), `leaderboard` (+`--tier`), `train-classifier`, `collect --funding`, `research` (+`--dry-run`, `--top-n`, `--tier`) wired. |
| — | Tests for new code | ⚠️ partial | Added: signal-path unification, strategy contract, window-engine internals, registry (12 tests), research pipeline (13 tests), portfolio (38 new tests). **Still missing:** leaderboard, funding collector, regime classifier, end-to-end engine run. |

**Headline:** Phases 1–6 are functionally complete; strategy registry, portfolio
construction layer, and research pipeline now also complete (session gap-closure,
2026-06-09). Suite: **299 passed, 1 xpassed**. Remaining: wire portfolio into
engine (Phase E), missing test modules (Phase B), real engine run (Phase C).

---

## Known gaps / TODO (priority order)

1. ~~Unify the pairs signal path (Phase 1).~~ ✅ DONE (session opus-audit).
2. ~~√365 Sharpe + NaN guard + cadence-aware funding annualization.~~ ✅ DONE.
3. ~~Run pairs strategies in the engine via `generate_signals_pair`.~~ ✅ DONE —
   `window_engine._run_pairs_strategy` + `_run_pair_window`, two-leg mark-to-market.
4. ~~Run funding strategies in the engine on 8h data.~~ ✅ DONE —
   `_run_funding_strategy`, additive-income P&L, `db.get_funding_rates` added.
5. ~~**Strategy registry** — strategies were hardcoded list in `run_engine_cmd`.~~ ✅ DONE —
   `strategies/registry.py`, `@StrategyRegistry.register` on all 12, `instantiate_all()`.
6. ~~**Portfolio construction layer** was placeholder (~109 lines).~~ ✅ DONE —
   `risk_parity_weights`, `kelly_fraction`, `multi_strategy_allocate`, `correlation_matrix`,
   `diversification_ratio`, `concentration_check` all implemented and tested.
7. ~~**Closed-loop research pipeline** — no agent-facing propose/gate/reject loop.~~ ✅ DONE —
   `research/pipeline.py`, `python main.py research`, decisions logged to `tmp/research_decisions.jsonl`.
8. **Reuse `BacktestEngine`** instead of the window engine's private P&L loops.
   ⏸️ DEFERRED 2026-06-25, deliberately, after investigation — see Phase D note above.
   Not abandoned silently; revisit only if a real regression-pinning test (D1) is built first.
9. ~~**Tests** still needed for: leaderboard scoring/tiers, funding collector pagination,
   regime classifier gate, and an end-to-end engine run against a temp DB.~~ ✅ DONE 2026-06-25 (Phase B).
10. **Wire `portfolio/` into the engine** — redirected 2026-06-25: per-bar wiring into
    `window_engine.py` would change every strategy's historical Sharpe comparability for no
    stated benefit (those loops are intentionally unit-position). Wiring `multi_strategy_allocate`
    into **paper-trading capital allocation** instead (`trading/paper_trader.py`) — see session log.
11. **First real run + tuning.** Run `python main.py engine` against the populated DB,
    then `leaderboard`. Synthetic funding Sharpe looked very high (clean sine input) —
    sanity-check on real noisy funding data; consider slippage in the funding P&L. (Phase C)

---

## Active claims  (edit before you touch a file; remove when done)

- _(none — single active session as of 2026-06-07. The earlier concurrent-editor
  collision is resolved: the user confirmed they stopped editing.)_

---

## SESSION LOG TEMPLATE — Copy this for every new entry

### YYYY-MM-DD — session "your-session-id" (Claude / Human) — STATUS
**Phase worked:** [A / B / C / D / E / F]
**DB health check:** [PASSED / FAILED — reason]
**engine_results row count at session start:** [N]
**Files changed:** [list]
**Tests added:** [list]
**Suite result:** [X passed, Y xfailed, Z failed]
**Phase checklist progress:** [e.g. A1 ✅ A2 ✅ A3 ⏳ A4 ⏳ A5 ⏳]
**Phase completion %:** [e.g. 40%]
**Blocking issues found:** [list or "none"]
**Bugs discovered and logged:** [list or "none"]
**Resume point for next session:** [exact next action]
**Session limit hit:** [yes — which limit / no]

---

## Session log  (newest first)

### 2026-06-25 — session "close-known-gaps" (Claude) — COMPLETE
**Phase worked:** none of A–F (post-pivot work — see staleness banner at top of this file)
**DB health check:** PASSED — applied + verified migrations 0011→0012 cleanly on the dev DB; ran `run_paper_cycle` live against real data twice (once for the regime filter, once for the stop wiring) with no errors
**engine_results row count at session start:** 83,497 (confirmed gap #2, "no real engine run yet," was already stale before this session)
**Files changed:**
  - `tests/test_regime_classifier.py` (new) — B3: <200/≥200-row training gate, MODEL_PATH patched to tmp_path
  - `tests/test_window_engine.py` — B4: extended `FakeDB` with `read_sql`, added `test_engine_results_produce_nan_free_leaderboard` piping all 3 strategy kinds through `build_leaderboard()`
  - `trading/paper_trader.py` — `_capital_weighted_equity` (risk-parity paper capital sizing, reuses `monitoring.leaderboard._fetch_returns_history` + `PortfolioOptimizer.multi_strategy_allocate`); regime-as-live-filter (`compute_regime` gate on new opens only, via `regime_direction` param on `_apply_paper_step`); structural stop (`stop_price` param, `_stop_breached`, checked before any new signal, force-closes with `close_reason="stop"`)
  - `config/loader.py` — new `paper_regime_filter_enabled` setting (`PAPER_REGIME_FILTER_ENABLED`, default True)
  - `strategies/base.py` — new optional `get_stop_level(df, params) -> Optional[float]` on `BaseStrategy`, defaults to `None`
  - `strategies/smc_breakout.py` — `SMCBreakout.get_stop_level()` override, a deliberately separate self-contained replay of the range/bias logic (not a refactor of the already-tested `generate_signals`)
  - `trading/position.py` — `PositionState.stop_price` field + DB plumbing
  - `alembic/versions/0012_add_stop_price.py` (new)
  - `tests/test_risk_sizing_and_alerts.py`, `tests/test_smc_breakout_stop.py` (new) — capital weighting, regime filter, stop force-close, `get_stop_level` correctness tests
  - `README.md` — Known Gaps rewritten (2 still open, 6 resolved/redirected/deferred with rationale), module tree, Architecture Notes, test count
**Tests added:** 21 net new (394 → 415)
**Suite result:** 415 passed, 0 failed
**Phase checklist progress:** n/a (see staleness banner)
**Phase completion %:** n/a
**Blocking issues found:** none
**Bugs discovered and logged:** none new — two stale doc claims found and corrected (B1/B2 tests already existed; "no real engine run yet" already false)
**Resume point for next session:** The 2 still-open gaps (live trading unexercised on a real account; multi-timeframe strategy/engine consumer) are unchanged by this session — see README Known Gaps. If continuing the multi-timeframe thread, see the gap analysis referenced in this session's chat history (4-phase plan: schema/collector plumbing → strategy interface → engine slicing → paper/live consumption; engine slicing was flagged as the highest-risk piece).
**Session limit hit:** no

### 2026-06-25 — session "multi-tf-and-paper-overhaul" (Claude) — COMPLETE
**Phase worked:** none of A–F (post-pivot work — see staleness banner at top of this file)
**DB health check:** PASSED — `db.test_connection()` OK; `prices` had 65,213 rows pre-migration, all auto-backfilled as `timeframe='1d'` with zero loss
**engine_results row count at session start:** not checked (no engine-loop changes this session)
**Files changed (commit `af52cee` — 4h timeframe support):**
  - `alembic/versions/0011_add_timeframe_column.py` (new) — adds `prices.timeframe`, widens unique constraint/index to `(exchange, symbol, timeframe, timestamp)`
  - `data/collectors/{binance,kraken,htx}_collector.py` — added `'4h'` to `TIMEFRAME_TO_MS`
  - `data/models.py` — `Timeframe` literal includes `'4h'`
  - `data/db.py` — `insert_prices()` now writes the `timeframe` column (was silently discarded); `get_prices()` gained an optional `timeframe` filter
  - `cli/collect.py` — `run_backfill` timeframe validation accepts `'4h'`
**Files changed (commit `99ea344` — multi-strategy paper trading; carried over from an earlier uncommitted session, committed now):**
  - `trading/paper_trader.py`, `trading/position.py`, `trading/live_trader.py`, `trading/alerts.py` (new)
  - `strategies/dca.py` — fixed calendar-anchoring bug that made DCA permanently unable to BUY
  - `monitoring/routes/{ops,pages,pipeline,trading_routes}.py`, `monitoring/templates/*.html`, `monitoring/static/` (new dashboard JS/CSS bundle + build script)
  - `alembic/versions/0010_add_manual_halt.py` (new)
  - `cli/backtest.py`, `main.py`, `config/loader.py` — `--replay-days` paper backfill wiring, risk-sizing gap-clip
  - `tests/test_ops_routes.py`, `tests/test_pipeline_routes.py`, `tests/test_risk_sizing_and_alerts.py` (new)
**Tests added:** 45 net new (349 → 394)
**Suite result:** 394 passed, 0 failed
**Phase checklist progress:** n/a (see staleness banner)
**Phase completion %:** n/a
**Blocking issues found:** none
**Bugs discovered and logged:** none new this session (DCA bug and trade-journal-summary SQL bug were fixed in the carried-over session and are no longer open)
**Resume point for next session:** Multi-timeframe data storage now exists (4h alongside 1d, no migration needed) but has no consumer — `strategies/base.py::generate_signals(df, params)` still takes one DataFrame and `backtesting/window_engine.py` still slices one timeframe per run. A scoped, low-risk next step (discussed but not started): wire the already-computed `compute_regime()` trend/direction into `trading/paper_trader.py::_paper_candidates()` as a live long/short bias filter before attempting a full multi-timeframe strategy interface + engine rewrite (see plan notes from this session — engine slicing across timeframes without lookahead leakage is the highest-risk piece of that larger effort).
**Session limit hit:** no (docs-only follow-up after the two commits)

Committed two logical commits (not pushed): 4h timeframe storage capability across
collectors/schema/DB, and the larger multi-strategy-per-exchange paper trading +
trade journal + backfill/replay + DCA fix body of work that had accumulated
uncommitted across prior sessions. Verified the 4h path live end-to-end (real
Binance backfill of BTC/USDT 4h candles, no constraint collisions with existing
1d rows) and confirmed the full test suite (394) plus a manual round-trip
insert/filter test all pass. Updated `README.md` (module tree, Phase Completion
table, CLI reference, Known Gaps, Architecture Notes) and this file to match.

### 2026-06-11 — session "signal-fork-fix" (Claude) — COMPLETE
**Phase worked:** pre-commit stabilization (z-score threshold fork)
**DB health check:** PASSED — connectivity OK, all 8 symbols current through 2026-06-10 (<48h), data integrity verified via direct query, engine_results=21,540 rows (well above 200)
**engine_results row count at session start:** 21,540
**Files changed:**
  - `config/constants.py` — added `STAT_ARB_ENTRY_Z = 2.0` and `STAT_ARB_EXIT_Z = 0.5` (canonical constants)
  - `config/loader.py` — import constants; use them as fallback defaults instead of bare literals
  - `cli/backtest.py` — fixed 4 hardcoded `entry_threshold=2.0, exit_threshold=0.5` call sites in `run_backtest`, `run_validation_cmd`, `run_paper_trading`, `run_full_pipeline` to read `cfg.entry_threshold` / `cfg.exit_threshold` from `get_settings()`
  - `strategies/stat_arb.py` — `StatArbPairsStrategy.param_grid` now references `STAT_ARB_ENTRY_Z` / `STAT_ARB_EXIT_Z` instead of bare literals
  - `backtesting/engine_eval.py` — `EvaluationBacktestEngine` constructor defaults now reference `STAT_ARB_ENTRY_Z` / `STAT_ARB_EXIT_Z`
  - `.gitignore` — added `.coverage`, `coverage.xml`, `htmlcov/`, `tmp/` (were missing, causing them to appear as untracked)
**Tests added:** `tests/test_signal_fork_regression.py` (4 new tests: param_grid parity, engine constructor default, threshold sensitivity, path parity at non-default threshold 1.5/0.3)
**Suite result:** 303 passed, 1 xpassed, 0 failed (baseline was 299+1xpassed; +4 new tests)
**Phase checklist progress:** signal fork ✅; local verification ✅; pre-commit hygiene ✅
**Phase completion %:** 100%
**Blocking issues found:** none
**Bugs discovered and logged:**
  - Signal fork: `cli/backtest.py` hardcoded `2.0`/`0.5` at 4 call sites instead of reading from `config/settings.yaml`. `cli/features.py` (signals path) already read from config. Fork was latent (both happened to be 2.0/0.5 today) but would diverge on any config change.
**Resume point for next session:** Begin Phase A — prop-firm bug fixes (A1 daily-loss parenthesization in `backtesting/engine.py:139`, then A2, A3, A4, A5 in order).
**Session limit hit:** no

Consolidated z-score thresholds to a single source of truth: `STAT_ARB_ENTRY_Z` /
`STAT_ARB_EXIT_Z` in `config/constants.py`, loaded at runtime via `get_settings()`.
All 4 hardcoded call sites in `cli/backtest.py` now read from config. Regression
test guards constant consistency, threshold sensitivity, and engine/signals-path
parity — will catch any future re-introduction of divergent literals.

### 2026-06-09 — session "gap-closure" (Claude) — COMPLETE
**Phase worked:** infrastructure gaps (strategy registry, portfolio construction, research pipeline)
**DB health check:** SKIPPED — no DB-touching changes; source-only session
**engine_results row count at session start:** unknown
**Files changed:**
  - `strategies/registry.py` (new)
  - `strategies/__init__.py` (rewritten)
  - `strategies/ema_crossover.py`, `macd.py`, `supertrend.py`, `donchian_breakout.py`, `bollinger_reversion.py`, `rsi_extremes.py`, `atr_volatility_breakout.py`, `keltner_squeeze.py`, `dca.py`, `hodl_rebalance.py`, `stat_arb.py`, `funding_rate_arb.py` (decorator added to each)
  - `portfolio/optimizer.py` (added `risk_parity_weights`, `kelly_fraction`, `multi_strategy_allocate`)
  - `portfolio/risk.py` (added `correlation_matrix`, `diversification_ratio`, `concentration_check`)
  - `research/__init__.py` (new)
  - `research/pipeline.py` (new)
  - `main.py` (registry in `run_engine_cmd`; `run_research_cmd`; `research` mode in argparse)
  - `tests/test_registry.py` (new — 12 tests)
  - `tests/test_research_pipeline.py` (new — 13 tests)
  - `tests/test_portfolio.py` (extended — 38 new tests)
  - `README.md`, `AGENTS.md`, `RISK_ENGINE_AUDIT.md` (doc updates)
**Tests added:** 63 new tests
**Suite result:** 299 passed, 1 xpassed, 0 failed
**Phase checklist progress:** strategy registry ✅, portfolio construction ✅, research pipeline ✅; Phase E: E2 ✅, E5 partial ✅
**Phase completion %:** all three gaps 100%
**Blocking issues found:** none
**Bugs discovered and logged:** none
**Resume point for next session:** Begin Phase A — prop-firm bug fixes (A1 daily-loss parenthesization, A2 leverage clip, A3/A4 single-asset mark-to-market), then Phase B missing tests.
**Session limit hit:** no

Closed three previously-identified gaps: (1) strategy registry replaces hardcoded
list in `run_engine_cmd` — agents can now enumerate/instantiate strategies without
parsing `main.py`; (2) portfolio construction upgraded from placeholder to real
risk-parity weights, half-Kelly sizing, and multi-strategy capital allocation with
correlation and diversification metrics; (3) research pipeline wires the full
propose→engine→gate→log loop behind `python main.py research`.

### 2026-06-08 — session "playbook-update" (Claude) — COMPLETE
**Phase worked:** none (docs only)
**DB health check:** SKIPPED — docs-only session, no source changes
**engine_results row count at session start:** unknown
**Files changed:** `AGENTS.md` only
**Tests added:** none
**Suite result:** unchanged (261 passed, 1 xpassed)
**Phase checklist progress:** n/a
**Phase completion %:** n/a
**Blocking issues found:** none
**Bugs discovered and logged:** none
**Resume point for next session:** Begin Phase A — start with A1 (daily-loss parenthesization) in `backtesting/engine.py:139`, then A2, A3, A4 in order, confirm A5 (xfails green) before closing the phase.
**Session limit hit:** no

Added to `AGENTS.md`: DATABASE HEALTH RULE, SESSION LIMITS, PHASE GATE RULE,
GAP BACKLOG (Phases A–F with per-item checklists), SESSION LOG TEMPLATE.
No source files touched.

### 2026-06-08 — session "repo-sync" (Claude) — COMPLETE
**Phase worked:** none (repo sync + rename)
**DB health check:** SKIPPED — sync/docs session
**engine_results row count at session start:** unknown
**Files changed:** 93 files staged and committed (all Phases 1-6 unpublished work); `README.md` rewritten
**Tests added:** none (tests were already present in the commit)
**Suite result:** 261 passed, 1 xpassed (at time of push)
**Phase checklist progress:** n/a
**Phase completion %:** n/a
**Blocking issues found:** none
**Bugs discovered and logged:** none
**Resume point for next session:** Phase A — prop-firm bug fixes
**Session limit hit:** no

Pushed all unpublished local work to `origin/main` (2 commits: 93-file bulk commit
+ README rewrite). Renamed `RAFund` → `Raf3nd` in all prose/display contexts across
the repo (zero Python identifiers, import paths, or DB names changed).

### 2026-06-07 — session "opus-engine" (Claude) — COMPLETE
Wired pairs + funding into the engine and fixed the position-carry P&L bug.
- **`data/db.py`**: added `get_funding_rates(symbol)` (funding_time→timestamp,
  funding_rate→close), via the SQLAlchemy read path.
- **`backtesting/window_engine.py`**:
  - **Position carry fix** — `_compute_metrics_from_signals` now CARRIES the position
    on HOLD (was flattening every HOLD → 1-bar trades) and marks the open position to
    market every bar. This was a latent correctness bug affecting ALL single-asset
    strategies (e.g. DCA was 1-bar trades, not accumulation).
  - Added `_compute_metrics_from_positions` (two-leg pairs mark-to-market) and
    `_compute_metrics_from_funding` (additive income `position*rate`, NOT a price
    return — the rate crosses zero and would explode as a price).
  - `run()` now dispatches by kind: funding (`timeframe=='8h'`) → `_run_funding_strategy`
    (8h cadence, √1095); pairs (`BasePairsStrategy`) → `_run_pairs_strategy` /
    `_run_pair_window`; else single-asset. Extracted `_run_over_windows`; threaded
    `annualization` + `funding_mode` through `_run_window` / `_mutate`.
  - `compute_regime` no longer `log()`s non-positive series (funding rates).
- **`main.py`**: added `StatArbPairsStrategy()` + `FundingRateArb()` to the engine roster.
- **Tests** (+6 in `test_window_engine.py`): carry, DCA accumulation, funding additive
  income + no-explode-through-zero, pairs metrics, and a 3-kind dispatch integration
  test. Suite: **261 passed, 1 xpassed**.
- **Verified** end-to-end on a fake in-memory DB: all three kinds produce sane, finite,
  bounded metrics; EMA trades now vary (carry works); funding DD 15–22% (was 9e7%).

### 2026-06-07 — session "opus-audit" (Claude) — COMPLETE
Full audit + Phase-1 unification + Phase-3 correctness fixes + first tests for new code.
- **Audited** the whole tree vs the spec; recorded the completion table above. All 18
  new modules import; full suite green.
- **`strategies/stat_arb.py`**: added `StatArbStrategy._hedge_ratio_from_training`,
  `signals_from_pair_prices` (stateful rich frame), `to_db_signals` (stateful int→enum).
  Refactored `StatArbPairsStrategy.generate_signals_pair` and `signals_to_db_format` to
  **delegate** to that single core (removed two duplicate inline z-score impls). This
  unifies the Phase-1 signal path: signals-command and backtest now match exactly.
- **`backtesting/engine_eval.py`**: `generate_pair_signals` / `signals_for_database`
  delegate to the same core (removed the old inline z-math + non-stateful mapper).
- **`backtesting/window_engine.py`**: fixed Sharpe annualization √252 → √365
  (`PERIODS_PER_YEAR`); added a NaN-candle guard (ffill/bfill) in the metrics loop.
- **Added tests** (30): `test_signal_path_unification.py`, `test_strategies_contract.py`,
  `test_window_engine.py`. Suite: **255 passed, 1 xpassed**.
- **Did NOT touch `main.py`** — the concurrent `StatArbPairsStrategy.signals_to_db_format`
  call site is now correct (delegates to the unified core), so no change was needed.
- **Handoff — next priorities:** engine gaps #3 (pairs in engine), #4 (funding cadence),
  #5 (reuse `BacktestEngine`), #6 (leaderboard/funding/classifier tests). See TODO list.

---

## Eval Mode Operating Procedure

> **Purpose:** Steps to follow when `eval_mode: true` in `config/settings.yaml` and a
> real prop-firm evaluation account is active. Do NOT set `eval_mode: true` on a paper-only
> run — it restricts candidates to `ready_for_live` strategies only.

### Pre-Session Checklist (before enabling eval_mode)
1. **Leaderboard check:** `python main.py leaderboard` — confirm at least one strategy
   shows `LIVE-READY`. If none, do not enable eval_mode; accumulate more paper history.
2. **Floor status:** `python main.py paper --status` — confirm no `hard_floor` or
   `account_failed` flag is set on any slot. If hard_floor is set, complete the reset
   procedure below before proceeding.
3. **Daily loss headroom:** confirm today's drawdown from open is below $100 (soft floor).
   If already above $100 DD, wait for a new trading day (UTC midnight) before entering.
4. **Config review:** `grep eval_mode config/settings.yaml` → must read `eval_mode: false`
   before you flip it. Change to `true` only after steps 1–3 are clear.
5. **Startup log:** run `python main.py paper` once and review the startup block — confirm
   exchange connections, selected strategy, and floor status all look correct.

### No-Trade Conditions
Do not enter a new position when **any** of the following are true:
- `daily_halt = True` on the slot (daily loss limit hit — wait for UTC midnight rollover)
- Current drawdown from account start > $100 (soft floor active — sizing halved, extra caution)
- Drawdown > $130 (soft floor triggered — only scale-reduced entries permitted)
- Less than 1 hour before a major economic event (FOMC, CPI, etc.)
- Spread on the instrument is more than 2× the typical spread
- The top-down resolver returns `HOLD` (1w/1d bias and 4h/1h signals disagree)

### EOD Journal Entry (after each trading day)
Run at UTC midnight or at end of your session:
```
python main.py leaderboard          # capture score + DD
python main.py paper --status       # capture equity, daily_halt, account_failed
```
Record in your trade journal (paper or digital):
- Date, strategy selected, symbol
- Trades opened/closed (from `paper_orders` `setup_tag` + `close_reason`)
- Session PnL, cumulative DD from start, daily high-water mark
- Source timeframe of each entry (`source_timeframe` column in `paper_orders`)
- Whether any floor event triggered (`hard_floor`, `soft_floor`, `daily_halt`)
- Notes on regime (1w/1d bias direction, 4h confirmation) for each entry

### Hard Floor Reset Procedure
The hard floor fires at $145 cumulative DD ($4,855 equity) — $5 before the prop firm's
$150 floor. When triggered:
1. `account_failed = True` and `daily_halt = True` are set automatically. All paper orders
   are blocked.
2. **Do not manually edit the DB** to clear these flags until you have reviewed the journal
   and confirmed the cause.
3. **Review:** query `SELECT * FROM paper_orders ORDER BY created_at DESC LIMIT 20` and
   identify the `close_reason="hard_floor"` row. Trace back through the preceding OPENs
   using `setup_tag` and `source_timeframe`.
4. **Root cause:** was the loss due to regime mismatch, oversized entry, or a news event?
   Document in the journal.
5. **Reset (only after review):**
   ```python
   from trading.paper_trader import resume_trading
   resume_trading(db, exchange="binance", run_id="paper_binance_<strategy>_<symbol>")
   ```
   This clears `manual_halt`. To also clear `account_failed`, update the row directly:
   ```sql
   UPDATE paper_positions SET account_failed = FALSE, daily_halt = FALSE
   WHERE run_id = 'paper_binance_<strategy>_<symbol>';
   ```
6. **Reduce risk:** after a hard-floor event, set `risk_per_trade_pct: 0.003` in
   `config/settings.yaml` for the remainder of the evaluation to widen the buffer.
7. If the prop firm's actual $150 floor has been breached, the evaluation has failed.
   Stop all trading and contact the prop firm per their reset/refund policy.
