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

## Spec completion status  (updated 2026-06-07, session "opus-audit")

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
| — | Tests for new code | ⚠️ partial | Added: signal-path unification, strategy contract, window-engine internals (30 tests). **Still missing:** leaderboard, funding collector, regime classifier, end-to-end engine run. |

**Headline:** Phases 1–6 are now functionally complete and the suite is green
(261 passed). Phase 1 unified; Phase 3 carry/annualization/NaN bugs fixed and pairs
+ funding wired into the engine. **Remaining:** reuse `BacktestEngine` instead of the
engine's private P&L loop (architectural, optional); tests for leaderboard/funding
collector/classifier; a real end-to-end engine run against a populated DB.

---

## Known gaps / TODO (priority order)

1. ~~Unify the pairs signal path (Phase 1).~~ ✅ DONE (session opus-audit).
2. ~~√365 Sharpe + NaN guard + cadence-aware funding annualization.~~ ✅ DONE.
3. ~~Run pairs strategies in the engine via `generate_signals_pair`.~~ ✅ DONE —
   `window_engine._run_pairs_strategy` + `_run_pair_window`, two-leg mark-to-market.
4. ~~Run funding strategies in the engine on 8h data.~~ ✅ DONE —
   `_run_funding_strategy`, additive-income P&L, `db.get_funding_rates` added.
5. **Reuse `BacktestEngine`** (single-asset marked-to-market + fractional sizing are
   now fixed) instead of the window engine's private P&L loops — removes the remaining
   P&L fork (single-asset, pairs, and funding each have their own mark-to-market loop).
   Optional/architectural; current loops are tested and produce sane results.
6. **Tests** still needed for: leaderboard scoring/tiers, funding collector pagination,
   regime classifier gate, and an end-to-end engine run against a temp DB.
7. **First real run + tuning.** Run `python main.py engine` against the populated DB,
   then `leaderboard`. Synthetic funding Sharpe looked very high (clean sine input) —
   sanity-check on real noisy funding data; consider slippage in the funding P&L.

---

## Active claims  (edit before you touch a file; remove when done)

- _(none — single active session as of 2026-06-07. The earlier concurrent-editor
  collision is resolved: the user confirmed they stopped editing.)_

---

## Session log  (newest first)

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
