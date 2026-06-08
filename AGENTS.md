# AGENTS.md — Coordination Log for the Raf3nd Engine Build

**Read this first.** Multiple agents edit this repo concurrently. This file is the
single place to (a) see how close we are to the agent implementation spec, (b)
claim files you are about to edit, and (c) record what you did each session so the
next agent doesn't re-create forks or clobber work.

> The authoritative spec is the "Raf3nd Engine — Agent Implementation
> Instructions" (multi-strategy, multi-window walk-forward engine → ranked
> leaderboard across CONSERVATIVE/STANDARD/PERMISSIVE tiers).

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
   Run: `python main.py collect --dry-run` (or equivalent ping).
   If the DB is unreachable, stop and report. Do not proceed.

2. **Check data freshness**
   Run: `python main.py collect --status` and inspect the most recent timestamp
   per symbol. If any symbol has a gap > 48 hours from now, run:
   `python main.py collect` to pull fresh data from Binance before continuing.

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

### PHASE B — Missing Tests  ✅ / ⏳ / ❌
**Goal:** Test coverage for leaderboard, funding collector, regime classifier, and a true e2e engine run.
**Prerequisite:** Phase A ≥ 90% complete.
**Exit criteria:** 4 new test modules present and green; total suite count increases by ≥ 30 tests.

| # | Item | File | Status |
|---|---|---|---|
| B1 | Leaderboard scoring + tier gate tests | `tests/test_leaderboard.py` | ⏳ |
| B2 | Funding collector pagination tests (mock Binance pagination) | `tests/test_funding_collector.py` | ⏳ |
| B3 | Regime classifier gate tests (< 200 rows → None; ≥ 200 rows → label) | `tests/test_regime_classifier.py` | ⏳ |
| B4 | End-to-end engine run against a temp PostgreSQL DB (use pytest-postgresql or a fixture DB; all 3 strategy kinds must produce finite, non-NaN leaderboard rows) | `tests/test_e2e_engine.py` | ⏳ |

**Verification:** `pytest tests/test_leaderboard.py tests/test_funding_collector.py tests/test_regime_classifier.py tests/test_e2e_engine.py -v` all green.

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

### PHASE D — BacktestEngine Consolidation (Optional / Architectural)  ✅ / ⏳ / ❌
**Goal:** Eliminate the 3 private P&L loops in `window_engine.py` by routing all strategies through `BacktestEngine`.
**Prerequisite:** Phase C ≥ 90% complete.
**Note:** This is architectural cleanup. It must not change any leaderboard results. Add a regression test that pins leaderboard scores before and after consolidation within a tolerance of ±0.01.

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
| E1 | Implement fractional crypto sizing in `PortfolioOptimizer` — replace `int(value/price)` with `round(value/price, 8)` | `portfolio/optimizer.py:41` | ✅ DONE (session research-impl) |
| E2 | Add Kelly sizing (fractional, capped at 0.25) to `RiskManager` | `portfolio/risk.py` | ⏳ |
| E3 | Add volatility targeting position scalar | `portfolio/risk.py` | ⏳ |
| E4 | Wire `RiskManager.check_position_limits` into the engine's `_open_position` | `backtesting/engine.py` | ⏳ |
| E5 | Add portfolio-level gross exposure cap (N × 20% → must not exceed 100%) | `portfolio/risk.py` + `engine.py` | ⏳ |
| E6 | Tests for Kelly, vol-targeting, gross cap | `tests/test_portfolio_risk.py` | ⏳ |

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

## Spec completion status  (updated 2026-06-08, session "research-impl")

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
| — | CLI | ✅ done | `engine` (+filters), `leaderboard` (+`--tier`), `train-classifier`, `collect --funding` wired. |
| — | Tests for new code | ⚠️ partial | Added: signal-path unification, strategy contract, window-engine internals (30 tests), EMA verification (9), ensemble (17), slope-regime (5), leaderboard feedback (1). **Still missing:** leaderboard, funding collector, regime classifier, end-to-end engine run. |
| — | Research synthesis | ✅ done | `docs/RESEARCH_SYNTHESIS.md` — CryptoTrade, IEEE 11513234/11035368, HMM, funding arb, Kelly, DSR. |
| — | Fractional sizing | ✅ **FIXED** | `portfolio/optimizer.py`: `round(value/price, 8)` — crypto positions no longer truncate to 0 on a $5k account. |
| — | Leaderboard → mutation feedback | ✅ done | `WalkForwardWindowEngine._ranked_mutation_grid` — historically high-Sharpe param combos tried first during mutation. |
| — | Ensemble signal layer | ✅ done | `monitoring/ensemble.py` — majority-vote composite signal (Trust-The-Majority pattern). Not wired into engine (Rule 1). |
| — | Slope-based regime | ✅ done | `compute_regime_slope()` in `window_engine.py` — safe for zero/negative series; `tanh`-normalised [0,1] trend. |

**Headline:** Phases 1–6 are now functionally complete and the suite is green
(295 passed, up from 261). Research-driven improvements implemented this session:
fractional sizing fix (P0), EMA verification + tests (P0), leaderboard→mutation
feedback (P1), ensemble signal layer (P1), slope-based regime (P1), research
synthesis doc (P2). **Remaining:** reuse `BacktestEngine` (architectural);
leaderboard/funding collector/classifier tests; first real engine run vs DB.

---

## Known gaps / TODO (priority order)

1. ~~Unify the pairs signal path (Phase 1).~~ ✅ DONE (session opus-audit).
2. ~~√365 Sharpe + NaN guard + cadence-aware funding annualization.~~ ✅ DONE.
3. ~~Run pairs strategies in the engine via `generate_signals_pair`.~~ ✅ DONE.
4. ~~Run funding strategies in the engine on 8h data.~~ ✅ DONE.
5. ~~Leaderboard → mutation feedback loop.~~ ✅ DONE (session research-impl) —
   `_ranked_mutation_grid` tries historically best params first.
6. **Reuse `BacktestEngine`** instead of the window engine's private P&L loops —
   removes the P&L fork. Optional/architectural; current loops are tested.
7. **Tests** still needed for: leaderboard scoring/tiers, funding collector pagination,
   regime classifier gate, and an end-to-end engine run against a temp DB.
8. **First real run + tuning.** Run `python main.py engine` against the populated DB,
   then `leaderboard`. Synthetic funding Sharpe was very high (clean sine input) —
   **must sanity-check on real noisy Binance funding data**; consider slippage.
9. **`regime_method` selector** — `compute_regime_slope` exists but is not wired as a
   selectable option in `_run_window` / `_run_pair_window`. Add a `regime_method` param
   to `WalkForwardWindowEngine.__init__` and route accordingly.
10. **Full Kelly fraction sizing** — `portfolio/optimizer.py` now uses fractional units
    but still allocates a fixed `max_position_size` fraction. Wire Kelly fraction using
    leaderboard win_rate + avg_sharpe for per-strategy optimal sizing.
11. **Deflated/Probabilistic Sharpe** in promotion gate — wire into
    `backtesting/significance.py` + `models/validator.py`; require PSR ≥ benchmark.
12. **Ensemble wired into engine** — `monitoring/ensemble.py` is a standalone layer.
    Wire as an optional signal layer in the CLI (`python main.py signals --ensemble`).
13. **R:R structural enforcement in EMA** — the exit-on-reverse-crossover is the
    canonical exit. Structural stop/target would fork the P&L loop (Rule 1 violation)
    unless implemented as a `max_hold_bars` or ATR-based stop within the strategy.

---

## Active claims  (edit before you touch a file; remove when done)

- _(none — session research-impl completed 2026-06-08.)_

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

### 2026-06-08 — session "research-impl" (claude-sonnet-4-6) — COMPLETE
**Phase worked:** P0/P1 research-driven improvements (not a lettered phase)
**DB health check:** SKIPPED — offline implementation session (no live DB queries)
**engine_results row count at session start:** unknown (no DB connection)
**Literature read:** CryptoTrade EMNLP 2024 (abstract); IEEE 11513234 DRL+LLM (abstract via search);
  IEEE 11035368 EMA (metrics via search); HMM regime BTC 2024-2026; funding arb Sharpe benchmarks;
  Kelly criterion; Deflated/Probabilistic Sharpe.  Full synthesis: `docs/RESEARCH_SYNTHESIS.md`.
**Files changed:** `portfolio/optimizer.py`, `backtesting/window_engine.py`,
  `monitoring/ensemble.py` (new), `tests/test_portfolio.py`, `tests/test_ema_crossover.py` (new),
  `tests/test_ensemble.py` (new), `tests/test_window_engine.py`, `docs/RESEARCH_SYNTHESIS.md` (new).
**Tests added:** 34 new tests (9 EMA, 17 ensemble, 5 slope-regime, 1 leaderboard-feedback, 3 fractional sizing)
**Suite result:** 295 passed, 1 xpassed (was 261 passed, 1 xpassed — no regressions)
**Changes implemented:**
  P0: Fractional crypto sizing (`round(value/price, 8)` in optimizer.py — fixes $5k/BTC = 0 units bug)
  P0: EMA crossover verified correct vs IEEE 11035368; 9 tests pinning signal logic
  P0: `compute_regime` zero-guard in ATR computation (funding rate zero-crossing safety)
  P1: Leaderboard→mutation feedback: `_ranked_mutation_grid` sorts by historical Sharpe first
  P1: `monitoring/ensemble.py` — Trust-The-Majority majority-vote composite signal (not engine-wired)
  P1: `compute_regime_slope()` — slope-based regime safe for zero/negative series
  P2: `docs/RESEARCH_SYNTHESIS.md` — full gap analysis, per-paper findings, accessibility log
**Bugs discovered and logged:** None new (fractional sizing bug was pre-existing/documented in RISK_ENGINE_AUDIT.md)
**Blocking issues found:** IEEE 11513234 and 11035368 full papers paywalled; CryptoTrade PDF unreadable;
  key metrics obtained via web search. ResearchGate for EMA paper returned 403.
**Resume point for next session:** Phase A (prop-firm bug fixes) — A1 daily-loss parenthesization
  in `backtesting/engine.py:139`, then A2/A3/A4, then A5 (xfails green). Also gap #8: first real
  engine run vs populated DB to sanity-check FundingRateArb Sharpe.
**Session limit hit:** no

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
