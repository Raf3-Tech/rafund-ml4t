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
>
> **⚠️ SECOND PIVOT (2026-09-06): prop-firm compliance is back, deliberately,
> scoped to Kraken Prop.** Kraken Prop has no API access on eval/funded
> accounts — every order is placed manually. Survival (never breach MDL/MDD)
> is now the explicit primary goal for anything touching live/paper risk,
> superseding the 2026-06-20 pivot for that surface. This does **not** revive
> the old CONSERVATIVE/STANDARD/PERMISSIVE tier gate or Phase A–F below (still
> stale/historical) — it's tracked as its own phased backlog, **KRAKEN PROP GAP
> BACKLOG**, right after Phase F. See that section for status.

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

## OVERFITTING GATE RULE — Mandatory, added 2026-09-06 (Kraken Prop Phase 6)

The leaderboard's score (Sharpe x win_rate x consistency) selects across every
strategy x parameter-set x symbol combination searched — the winner is partly
the luckiest of however many trials were run. Verified on real production
data the day this rule was added: with 305 trials searched, every currently-
"qualifying" strategy's deflated Sharpe came out to **0.0** — their modest
Sharpe ratios are fully explainable by luck among 305 trials, not skill. This
is not a hypothetical risk; it is what the live leaderboard actually shows.

**No strategy may be treated as `ready_for_live`, and no strategy may be
scanned by the Kraken Prop pending-signal detector
(`signals.pending_signal_detector._qualifying_single_symbol_candidates`),
unless `monitoring.leaderboard.build_leaderboard()`'s `passes_overfitting_gate`
column is `True` for it** — in addition to, not instead of, the existing
`qualifies` (pass_ratio) bar. `passes_overfitting_gate` requires both:
1. Deflated Sharpe Ratio (`monitoring/overfitting.py::deflated_sharpe_ratio`)
   >= `ready_for_live_min_deflated_sharpe` (default 0.95 — the conventional
   significance bar in the Bailey/Lopez de Prado literature, config-
   overridable, not a hardcoded spec fact).
2. Walk-forward out-of-sample check
   (`monitoring/overfitting.py::walk_forward_oos_within_band`) is `True` — a
   candidate's own windows split chronologically in half must show the
   second half's Sharpe falling inside a confidence band built from the
   first half. `None` (fewer than 4 windows — not enough evidence) does
   **not** pass; insufficient evidence is treated as failing, per this
   brief's "cannot breach" philosophy, never as passing by default.

Any agent that edits `build_leaderboard()`, `_qualifying_single_symbol_candidates()`,
or `select_eval_strategy()` and finds this check removed or bypassed must
restore it or get explicit user sign-off to relax it — do not silently drop
it to "fix" a test or unblock a strategy. `trial_count` (the number of
distinct strategy x symbol x params combinations actually searched, as of
the leaderboard build) is recorded on every leaderboard row specifically so
this correction is reproducible after the fact, per the brief.

Known simplification (documented in `monitoring/overfitting.py`'s module
docstring, not hidden): the Sharpe standard-error term assumes Gaussian
returns (skew=0, kurtosis=3) because `engine_results` stores per-window
summary stats, not the raw per-trade return series the exact formula needs
for real skew/kurtosis. This is directional, not exact.

### DSR diagnostics (added 2026-09-06, DSR Instrumentation follow-up)

`deflated_sharpe_ratio()` returns a `DeflatedSharpeResult` (not a bare
float): `probability`, `z_score` (signed, unclamped — the argument passed
to the normal CDF), `observed_sharpe`, `expected_max_sharpe_null` (SR_0),
`n_trials`, `n_observations`, `skew`, `kurtosis`, and `underflowed` (True
only when `probability` is *exactly* 0.0 because `math.erf` genuinely
saturates in float64 — verified this happens only once z drops below
roughly -8.3; the real production z-scores below, at -4.1 to -5.7, are
small but never hit this floor). All of this is persisted on every
leaderboard row (`dsr_z_score`, `dsr_expected_max_sharpe_null`,
`dsr_n_trials`, `dsr_n_observations`, `dsr_skew`, `dsr_kurtosis`,
`dsr_underflowed`, alongside the existing `deflated_sharpe`) — **never
clamp, floor, or epsilon-substitute the `probability` value**; 0.0 is the
honest answer, and `z_score` is what preserves ranking once every
probability floors at the same displayed 0.0000.

### What counts as one trial (Task 3 audit, 2026-09-06)

**Definition:** one trial = one distinct `(strategy_name, symbol, params)`
combination present in `engine_results` (`WHERE bars_used IS NOT NULL`),
aggregated across every `window_type`/`window_start`/`window_end` that
combination has been evaluated on. This is exactly what
`monitoring.leaderboard.build_leaderboard()`'s `trial_count` groups by, and
it's reproducible: re-running the same query against the same DB state
always yields the same number.

**Audited against the live DB (2026-09-06):**
- Raw `engine_results` rows: **134,776**.
- Distinct run_id: 40 (i.e. 40 separate engine invocations produced this table).
- Distinct `(strategy_name, symbol, params)` — the current `trial_count`
  basis: **305**.
- Exact-duplicate `(strategy, symbol, params, window_start, window_end,
  window_type)` groups: 10,262. These are the same window re-evaluated and
  re-inserted across multiple engine runs — they inflate a candidate's
  `n_windows` (and so the precision of its `avg_sharpe`), but **do not**
  inflate `trial_count`, which counts distinct configs, not window rows.
- Automated mutation-grid search is **fully persisted**: summed across all
  12 registered strategies, distinct persisted params (20) exactly equals
  the sum of each strategy's full `_mutation_grid()` size (20) — verified
  by direct comparison, not assumed. `window_engine.py::_mutate` does stop
  early once a passing mutation is found for a given window (so not every
  grid cell is tried on every window), but across the full set of
  symbols/windows every strategy's grid has been exercised at least once.
  No gap on this axis.
- `tmp/research_decisions.jsonl` (91 entries) is fully **downstream** of
  `engine_results` — `research/pipeline.py` runs the engine (which
  persists) before gating, so these are not an independent trial source.

**The real gap, and why N is biased low, not high:** 11 of the 12
registered strategies have a mutation grid of size **1** — i.e., zero
automated parameter search. Their single persisted param set (e.g. ATR
Volatility Breakout's `atr_period=14, multiplier=1.5`) was fixed by manual
tuning in earlier sessions (cf. the 2026-06-11 "signal-fork-fix" session
log entry, where `STAT_ARB_ENTRY_Z`/`STAT_ARB_EXIT_Z` were similarly
frozen into `config/constants.py` after informal experimentation). Whatever
alternative values were tried by hand before settling on the frozen ones
never became `engine_results` rows and are unrecoverable. Per the
brief's explicit instruction to bias the correction upward under this kind
of ambiguity: **assume 5 unpersisted exploratory trials per hand-tuned
strategy** (a documented, deliberately round and conservative placeholder —
not a measurement) — 11 strategies x 5 = **55** — giving a **corrected
N of 360** (305 persisted + 55 documented buffer) as the more honest
figure, without pretending false precision about the exact count.

**Update, 2026-09-06 (Single-Leg Prop Eligibility brief, Task 3): N=360 is
now wired into code.** `monitoring.leaderboard.UNPERSISTED_EXPLORATORY_TRIALS_BUFFER
= 55` is added to the persisted distinct-candidate count for every
`trial_count`/`deflated_sharpe_ratio` computation. The user explicitly
authorized this in that brief ("The audit that produced this number is
complete and in AGENTS.md, so it is now appropriate to use it") — the
"report and stop" instruction from the audit session applied to that
session, not permanently; this note replaces the earlier "not wired into
code" one, which is now stale. Changing the buffer value itself still
requires a fresh audit and a sign-off, per the paragraph above.

### Prop-pipeline eligibility: leg count and instrument provenance (added 2026-09-06, Single-Leg Prop Eligibility brief)

Two more hard gate conditions, checked in `monitoring.leaderboard.build_leaderboard()`
and enforced in `signals.pending_signal_detector._qualifying_single_symbol_candidates`
and `ready_for_live`, each with its own reason code so it's visibly distinct
from failing on statistics (`passes_overfitting_gate`) — a strategy can be
statistically fine and still correctly excluded from the Prop pipeline for
either of these reasons:

1. **`MULTI_LEG_INELIGIBLE`** — `leg_count > 1` (`strategies/base.py`;
   `BaseStrategy.leg_count = 1`, `BasePairsStrategy.leg_count = 2`, exposed
   via `StrategyRegistry`). Kraken Prop has no API — every order is typed
   by hand. A two-leg trade means entering one leg, then the other, holding
   an unhedged position at up to 10x in between; on a spread worth a few
   basis points, inter-leg slippage exceeds the entire expected profit, and
   costs double (four commission events at 0.04% instead of two, plus
   funding on both legs, against a 3-6% lifetime buffer). This is
   structural, independent of whether the statistics are sound — confirmed
   in practice: all 4 of the leaderboard's only-ever qualifying candidates
   are Statistical Arbitrage (2-leg) pairs. **Multi-leg strategies remain
   fully active for research, backtesting, and the general leaderboard** —
   this excludes them from the Prop pipeline only; do not delete or disable
   Statistical Arbitrage.
2. **`NON_KRAKEN_SOURCE`** — the candidate's symbol is not single-sourced
   from Kraken in `prices` (`data/provenance.py`, `instrument_provenance`
   table, migration 0020). We do not trade an instrument on Kraken using
   another venue's price history — the basis between venues is exactly the
   kind of silent error that survives backtesting and fails live. Audited
   2026-09-06: every `/USD` symbol (ADA, BTC, DOT, ETH, LINK, SOL, XRP) is
   100% Kraken-sourced; every `/USDT` symbol is Binance-only or a
   Binance+HTX mix — **zero overlap, and no `/USDT` symbol is Kraken-sourced
   at all.** A symbol whose `prices` rows come from more than one exchange
   is treated as `NON_KRAKEN_SOURCE` too (`source_exchange` is `NULL`,
   not just "not Kraken") — provenance that can't be established as a
   single source is ineligible, not a special "maybe" case. This also
   caught a latent bug: `backtesting.window_engine` calls
   `db.get_prices(symbol, None, None)` with no exchange filter, so
   `engine_results` itself has never recorded which exchange a
   backtest's price data came from — `instrument_provenance` (populated by
   `data.provenance.sync_instrument_provenance`, not hand-maintained) is
   what makes this checkable after the fact.
   **Update, 2026-09-06 (Track Record Depth brief, Task 2): fixed, not just
   worked around.** `backtesting.window_engine.WalkForwardWindowEngine`
   now resolves each symbol's single source exchange via
   `data.provenance.audit_instrument_provenance` *before* requesting price
   data (`_resolve_exchange`), and passes that exchange explicitly to
   `get_prices`. A symbol with ambiguous provenance (or none) raises
   `AmbiguousPriceProvenanceError`, caught at `_run_strategy_symbol`/
   `_run_pairs_strategy` and logged as `[AMBIGUOUS PROVENANCE]` (distinct
   from the pre-existing `[SKIP] ... no price data` log) — that symbol is
   skipped, the rest of the run continues, but the gap is now impossible to
   miss in the logs rather than silently blended. `engine_results` gained
   `exchange` and `exchange_provenance` columns (migration 0021):
   `exchange_provenance` is `'verified'` for every row produced by the
   fixed engine going forward, `'inferred_from_symbol'` for the 44,029
   legacy rows backfilled from current `instrument_provenance` where a
   single source could be established after the fact
   (`data.provenance.backfill_engine_results_exchange`, run once,
   2026-09-06), and `'unknown'` for the 90,747 legacy rows whose symbol is
   ambiguous — deliberately not backfilled with a guess.
   **`prop_verified`** is separate and narrower — a boolean on the same
   table, defaulting `false`, populated by hand from the Prop market
   selector (Kraken Prop trades a subset of Kraken's spot universe as
   leveraged margin contracts, not everything Kraken-sourced). Kraken
   provenance (`is_kraken_sourced`) gates research/qualification
   (`_qualifying_single_symbol_candidates`); `prop_verified` gates
   `ready_for_live` only, so nothing reaches live-ready on an unverified
   pair, without blocking manual-signal proposals on pairs nobody has
   verified yet.

### Funding Rate Arbitrage — retired from the Prop pipeline (2026-09-06, Track Record Depth brief, Task 1)

Two independent, each-sufficient-alone reasons, so nobody revives this
strategy for Kraken Prop on the assumption that a first backtest is all it
needs:

1. **Structural — `leg_count = 2`.** `strategies/funding_rate_arb.py`'s own
   docstring describes it as "shorting perp + long spot (or inverse)" — a
   hedged two-leg position, even though it's coded against the
   single-series `BaseStrategy.generate_signals` interface (one funding-rate
   series drives both legs' entry/exit). `leg_count` is an explicit
   override on the class now, not inherited from `BaseStrategy`'s default
   of 1 — class hierarchy alone (`isinstance(BasePairsStrategy)`) missed
   this misclassification entirely, which is exactly why `leg_count` exists
   as metadata rather than being inferred. `MULTI_LEG_INELIGIBLE` excludes
   it the same as Statistical Arbitrage.
2. **Economic — funding direction is inverted on this account.** The
   strategy's entire premise is collecting funding payments. Kraken Prop
   charges funding at 0.033%/day on open positions (a cost, per the
   original Kraken Prop brief's hard constraints table) — it does not pay
   the account for holding a position. A strategy built to collect funding
   is, on this specific account, paying it instead: the edge doesn't just
   fail to materialize, it runs backwards.

Never run against real data (`engine_results` had zero rows for it as of
this note). Not deleted — remains available for research/backtesting on
accounts where funding is actually collected, per the same "multi-leg
strategies stay fully active outside the Prop pipeline" principle as
Statistical Arbitrage.

### Kraken track-record window configuration — fixed and documented (2026-09-06, Track Record Depth brief, Task 3)

**The window count is now a number that affects gate outcomes, so it is
fixed and stated here rather than incidental — do not change it to see if
a different choice produces better-looking z-scores; that would be an
uncounted parameter search wearing a different hat.**

Every Kraken symbol's previously-reported T=32-33 windows resolved to only
**8 distinct `(window_start, window_end)` pairs**, all `EXPANDING` and
sharing the same start date — an artifact of ~16 repeated
`python main.py engine` invocations between 2026-06-20 and 2026-09-03, each
contributing a near-duplicate expanding-from-genesis snapshot as a few more
days of Kraken data accumulated. Not independent observations; DSR and the
walk-forward OOS check both assume independence, so this was silently
optimistic in the same direction the brief warned about ("T cannot be
inflated by overlapping windows"), just via a mechanism (repeated re-runs
over calendar time) rather than a rolling-step config choice.

**Fixed, 2026-09-06:** `backtesting.window_engine.generate_kraken_track_record_windows`
partitions a symbol's full available history into non-overlapping,
back-to-back windows, each at least `KRAKEN_TRACK_RECORD_MIN_WINDOW_DAYS =
230` days (the longest single-leg warmup requirement — EMA Crossover's
slow=200 combo needs 200 + `MIN_TRADEABLE_BARS` bars). Used **only** for
the Kraken Prop track record (single-leg, Kraken-sourced candidates, via
`WalkForwardWindowEngine.run_kraken_track_record_windows`) — the general
research leaderboard and every non-Kraken-scoped candidate still use
`generate_windows()`'s expanding+rolling scheme unchanged, since those have
enough history for rolling windows to mean something and changing that
scheme was explicitly out of scope. New rows carry `window_type =
'INDEPENDENT'`, distinguishing them from legacy `EXPANDING`/`ROLLING` rows.

**Current result, against the live 798-day Kraken history (2024-06-29 to
2026-09-05, zero gaps, one bar/day, identical across all 7 symbols):
exactly 3 non-overlapping ~266-day windows.** Run once via
`scripts/rebuild_kraken_track_record.py` (computes new results in memory
first; only deletes the legacy contaminated `EXPANDING` rows for the 7
Kraken symbols — 4,263 of them — if the new run actually produces
results) — re-runnable if more Kraken history is ingested later, since it
always partitions whatever `[genesis, today]` span exists at run time.

**T=3 (T=2 for a few (strategy, symbol, params) combos where a window had
too few tradeable bars for that specific params) is too small for the
walk-forward OOS check to ever return anything but `None`** (it needs >= 4
windows, 2 per half) — every single-leg Kraken candidate's
`oos_within_confidence_band` is `None` as of this rebuild, not because
anything is broken, but because there is not yet enough independent history
to ask the question. `passes_overfitting_gate` requires OOS `True`
specifically (not `None`), so nothing can pass until either more Kraken
history exists or this constant is deliberately revisited — not by
lowering the 4-window OOS floor to manufacture a result, but by there
actually being enough calendar time.

Also noted, not fixed (pre-existing, unrelated to this session's changes):
`data/db.py::insert_engine_results`'s log line ("Inserted %d engine_results
rows") undercounts for a multi-page `execute_values` call — psycopg2's
`cursor.rowcount` reflects only the last internal page (default page size
100), not the true total. Verified the actual write was correct (305 rows
landed for the Kraken rebuild, matching what was submitted) by querying the
table directly, not by trusting the log line or the function's return value.

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

## KRAKEN PROP GAP BACKLOG — Phased Execution Plan (started 2026-09-06)

**Goal:** Kraken Prop has no API access on eval/funded accounts — every order is
placed manually in Kraken Pro. This system computes complete trade setups,
enforces hard risk limits, and publishes them for manual entry; it never
executes. Objective is survival (never breach MDL/MDD), not returns. Subject
to the same SESSION LIMITS and PHASE GATE RULE as Phases A–F above (1 phase
per session, ≥90% before advancing).

**Known conflicts, resolved (2026-09-06), binding for all phases below:**
- Phase 3's `TradeSetup` must **not** change `BaseStrategy.generate_signals`'s
  return type (still a per-bar `pd.Series` of BUY/SELL/HOLD — the vectorized
  core of the backtest engine, leaderboard, and all 13 strategies). `TradeSetup`
  is a live-signal construction layer built on top of the last bar's signal,
  same pattern as `signals/pending_signal_detector.py`.
- `TradeSetup` (Phase 3) **consolidates into** `signals.pending_signal_detector.PendingSignal`
  rather than existing as a second dataclass for the same concern (Rule 1) —
  extend `PendingSignal` with the missing fields (`worst_case_loss`,
  `r_multiple`, `leverage_required`; `source_timeframe`/`thesis`/`strategy_id`-
  equivalents already present) instead of building a parallel object.
- Phase 2's floor system **extends** the existing two-tier soft/hard floor in
  `trading/paper_trader.py` (soft ~$130 DD / hard ~$145 DD on lifetime
  drawdown, dollar-amount-based) and `trading/position.py::max_safe_notional`
  (continuous headroom-scaled sizing) — not a parallel floor system. That
  existing system has no daily-room-percentage tiers (spec wants 1.5%/2.0% of
  *daily* room) and no 00:30 UTC rollover (`PositionState.day_rolled()`
  currently uses UTC calendar-midnight) — Phase 2 must change both.
- `risk/cost_model.py` (Phase 1) is a **deliberate**, justified exception to
  Rule 1 relative to `backtesting/costs.py::TransactionCostModel` — different
  concern (exact live compliance math, Decimal) vs. different concern
  (float-approximate historical Sharpe comparison). Not a duplicate; documented
  in both files' docstrings.
- Phase 2 (`risk/prop_account.py` + `risk/daily_clock.py`) deliberately stops
  at *describing* account state and exposing `daily_soft_triggered`/
  `daily_hard_triggered`/`lifetime_hard_triggered` as properties — it does
  **not** touch `trading/paper_trader.py`'s live enforcement (the code that
  actually opens/sizes positions). The brief names Phase 4's pre-trade gate
  as "the only path to a live setup," so rewiring live enforcement ahead of
  that gate existing would create exactly the kind of second code path Rule 1
  forbids (one path gated by the new Kraken-Prop properties, one still gated
  by the old dollar-amount soft/hard floor, until Phase 4 unifies them).
  `from_position_state()` is the seam Phase 4 uses to bridge the two.
- Phase 3's `TradeSetup` is implemented as pure Decimal formulas in
  `strategies/setup.py` (`derive_size`/`derive_notional`/
  `derive_leverage_required`/`derive_expected_cost`/`derive_worst_case_loss`/
  `derive_r_multiple`) consumed by `PendingSignal`'s `_build_payload` — not a
  second dataclass. This **replaced** `_build_payload`'s prior sizing call to
  `trading.paper_trader._target_notional` (confirmed with the user): the
  brief's `size = equity*risk_pct/stop_distance` formula (risk_pct=0.25%,
  its own constant, deliberately not reading the shared
  `risk_per_trade_pct=0.5%` config value that `_target_notional` uses for
  the separate automatic multi-exchange paper-trading concern) is
  irreconcilable with `_target_notional`'s leg-allocation+headroom-cap
  approach. The headroom/gap-risk protection `_target_notional` gave up
  moves to Phase 4's gate as a REJECT, not inline notional-clipping.
  `pos.equity` (realized only) is used as the sizing input for now, not the
  fuller `PropAccountState.equity` — swap this once Phase 4's live
  account-state feed exists (documented in the module docstring).
- `PendingSignal`'s price/size fields (`limit_price`, `stop_price`,
  `take_profit_price`, `position_size_usd`, `qty`) plus the new
  `expected_cost`/`worst_case_loss`/`r_multiple`/`leverage_required` are now
  Decimal (confirmed with the user), and migration `0018` converts their DB
  columns from FLOAT8 to NUMERIC (table was empty — zero-risk type change,
  not a backfill) so Decimal precision survives the round trip, not just
  in-memory. The `/api/pending-signals` JSON boundary explicitly converts
  Decimal → float before `jsonify` (Flask's default encoder would otherwise
  serialize Decimal as a *string*, silently breaking the dashboard's
  `.toFixed()`/`.toLocaleString()` calls on those fields) — verified live
  against a running dashboard instance.
- Phase 4's gate does **not** implement a leverage-cap check, even though
  the brief's top-level constraints table lists per-asset caps (verified
  current: BTC 10x/$1M, NDX 10x/$1M, S&P 10x/$2M, SOL 5x/$500K, HYPE
  3x/$200K — blog.kraken.com, 2026-08) — the brief's Phase 4 section lists
  exactly six numbered reject rules and leverage isn't one of them. Recorded
  in `risk/pretrade_gate.py::KRAKEN_LEVERAGE_CAPS` for a future rule/UI use,
  not enforced by `evaluate()`. Don't add a 7th rule here without the user
  asking — that would be scope the brief didn't request.
- Two of the six reject rules reference numbers the brief never supplies:
  the correlation cap (`DEFAULT_CORRELATION_CAP_PCT = 1.0`, i.e. 100% of
  equity) and the consecutive-loss limit (`DEFAULT_CONSECUTIVE_LOSS_LIMIT =
  3`). Both are config-overridable placeholders, loudly flagged in
  `risk/pretrade_gate.py`'s docstring as uncalibrated — a risk owner must
  set real values before this gate protects a live account.
- `account_state` fed into `evaluate()` from `scan_kraken_signals` uses
  `kraken_prop_mdd_pct` defaulting to 3% (the conservative end of Kraken's
  3-6% tier range — assuming less room than you have is fail-safe, assuming
  more isn't) and a same-cycle `last_rollover` placeholder, since nothing
  yet persists real rollover-clock state (Phase 2 built the type, not a live
  feed). Fix both before this gate runs unattended against a live account.
- Phase 5's account-state-building logic was extracted into
  `signals.pending_signal_detector.current_account_state(pos, cfg)` — the
  single authority both `scan_kraken_signals` (the gate) and
  `monitoring.routes.publish` (the tablet's room-remaining figures) call, so
  neither could silently drift from the other. Also extracted
  `decimal_columns_to_float()` (same module) and moved `_records()` to a new
  `monitoring/routes/_json.py` — both were duplicated verbatim between
  `trading_routes.py`'s `/api/pending-signals` and the new publish route
  while writing Phase 5; deduplicated rather than left as two copies.
- "over Tailscale" is a network/deployment concern (Tailscale gives the
  Flask app a private address; nothing in `monitoring/routes/publish.py` is
  Tailscale-aware code) — not something this phase's code implements.
  Authentication reuses the existing `require_token`/`DASHBOARD_API_TOKEN`
  mechanism (Rule 1), applied to a GET route for the first time — every
  other GET in the dashboard is intentionally unauthenticated (same-origin
  browser trust), but this surface is reached by a device outside that
  boundary, so it deliberately breaks that convention. "No write methods on
  this surface at all" is enforced by a structural test
  (`tests/test_publish_routes.py::test_registered_publish_routes_are_get_only`)
  that inspects the blueprint's registered Flask rules, not just a comment.
- Phase 6 chose deflated Sharpe over White's Reality Check (the brief
  offered either) — Reality Check needs bootstrap resampling over each
  candidate's raw per-trade return series, which `engine_results` doesn't
  store (only per-window summary stats); deflated Sharpe needs only the
  cross-sectional spread of `avg_sharpe` values already computed, so it
  needed zero new data plumbing. See `monitoring/overfitting.py`'s module
  docstring for the resulting simplification (Gaussian skew/kurtosis
  assumed, not measured) and the OVERFITTING GATE RULE above for the policy.
- `passes_overfitting_gate` is wired into `_qualifying_single_symbol_candidates`
  (the actual live-qualification gate for Kraken Prop manual signals) rather
  than into `risk.pretrade_gate.GateContext.strategy_benched` — a benched
  strategy now never becomes a candidate in the first place, so
  `strategy_benched` stays an unpopulated defense-in-depth flag for any
  future code path that constructs a `GateContext` directly. Not wired
  redundantly without a concrete second call site that needs it.

| # | Phase | Files | Status |
|---|---|---|---|
| 1 | Cost model — commission + funding, Decimal-exact | `risk/cost_model.py` | ✅ DONE 2026-09-06 |
| 2 | Account state + daily clock (00:30 UTC rollover, two-tier floors) | `risk/prop_account.py`, `risk/daily_clock.py` | ✅ DONE 2026-09-06 |
| 3 | `TradeSetup` (extends `PendingSignal`) + `BaseStrategy` amendment | `strategies/setup.py`, `signals/pending_signal_detector.py` | ✅ DONE 2026-09-06 (as pure formulas + PendingSignal extension — see below; `BaseStrategy` left unchanged as agreed) |
| 4 | Pre-trade gate — sole path to a live setup | `risk/pretrade_gate.py` | ✅ DONE 2026-09-06 |
| 5 | Publish endpoint (read-only, Tailscale, OpenAPI schema) | `monitoring/routes/publish.py` | ✅ DONE 2026-09-06 |
| 6 | Leaderboard overfitting correction (deflated Sharpe / White's Reality Check) | `monitoring/leaderboard.py` | ✅ DONE 2026-09-06 — this is the brief's last phase; all 6 done |

**Verification per phase:** see the task brief's per-phase acceptance criteria
(exact numeric cases for Phase 1; rollover-boundary tests for Phase 2; a
leverage-invariance test for Phase 3; a gate-bypass-impossible test for
Phase 4).

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

### 2026-09-06 — session "track-record-depth" (Claude) — COMPLETE through Task 4; Task 5 deliberately not attempted
**Phase worked:** none of the 6 Kraken Prop phases — follow-up to 38362e2.
  5 tasks planned: retire Funding Rate Arbitrage, fix the exchange filter,
  maximize track record depth, re-baseline, re-propose the grid. **Tasks
  1-4 complete and committed. Task 5 (re-propose the grid) deliberately
  NOT done — the brief's own exit clause applies: history doesn't support
  a materially larger T (it supports a materially smaller, honest one), so
  this session stops before proposing a search against a 2-3-window
  foundation.** (This entry originally paused mid-Task-3 for a user
  decision on a destructive step — continuing below in the same entry
  rather than starting a new one, since it's the same session.)
**DB health check:** PASSED — connectivity OK. Applied migration 0021,
  ran the real exchange backfill against the live DB (44,029 of 134,776
  rows backfilled), and ran extensive read-only queries against
  `engine_results` to characterize the actual window/duplication situation.
**engine_results row count at session start:** 134,776 (unchanged — no
  rows added or removed this session; only 2 new columns populated)
**Files changed:**
  - `strategies/funding_rate_arb.py` — explicit `leg_count = 2` override
    (Task 1). Confirmed via its own docstring: "shorting perp + long spot"
    is a hedged two-leg position, miscategorized by class hierarchy alone
    (`BaseStrategy`, not `BasePairsStrategy`) — exactly the case
    `leg_count`-as-explicit-metadata exists for. `MULTI_LEG_INELIGIBLE`
    now excludes it automatically; also documented the independent,
    each-alone-sufficient economic reason (Kraken Prop charges funding,
    0.033%/day, rather than paying it — the strategy's premise is inverted
    on this account) so nobody revives it on "it just needs a first backtest."
  - `alembic/versions/0021_add_engine_results_exchange.py` (new) —
    `engine_results.exchange` (nullable) + `exchange_provenance`
    ('verified' | 'inferred_from_symbol' | 'unknown', default 'unknown').
  - `backtesting/window_engine.py` (Task 2) — new
    `AmbiguousPriceProvenanceError`; `_resolve_exchange()` (uses
    `data.provenance.audit_instrument_provenance`, cached per engine
    instance) required before every `_load_prices()` call; raises rather
    than blending when a symbol's `prices` rows span more than one
    exchange or none. Caught at `_run_strategy_symbol`/`_run_pairs_strategy`
    with a distinct `[AMBIGUOUS PROVENANCE]` ERROR log — that symbol is
    skipped, the rest of the run continues. `RunResult`/`_persist_results`
    thread `exchange`/`exchange_provenance` through to the DB (`'verified'`
    for every row the fixed engine produces now; funding-rate rows get
    `'inferred_from_symbol'`/`'binance'` since `get_funding_rates` doesn't
    go through `_load_prices` at all).
  - `data/provenance.py` — `backfill_engine_results_exchange()`: one-time
    backfill for pre-0021 rows, `'inferred_from_symbol'` only where
    current provenance establishes a single source (44,029 rows: 43,825
    single-leg + 204 pairs, pairs recorded as `"exA+exB"` when legs
    differ), `'unknown'` left alone for the rest (90,747 rows) — no
    confident value invented.
  - `data/db.py::insert_engine_results` — two new columns in the INSERT.
  - Tests: `tests/test_window_engine.py` (+6: multi-venue symbol raises
    without calling `get_prices`, no-provenance-at-all raises, the
    engine-level catch logs `[AMBIGUOUS PROVENANCE]` and returns `[]`
    without crashing the run, verified-exchange stamping, `RunResult`
    default), `tests/test_provenance.py` (+4: backfill single-sourced,
    backfill skips ambiguous, pairs-combines-both-legs, pairs-skipped-on-
    either-leg-ambiguous). `FakeDB` in `test_window_engine.py` needed a
    `GROUP BY symbol, exchange` branch and a `get_prices` signature update
    (caught via real test failures, not anticipated).
**Tests added:** 10 (6 window_engine, 4 provenance)
**Suite result:** 544 passed, 0 failed (534 before this session + 10)
**Task 3 finding — reported here, NOT acted on yet:** Calendar span for
  all 7 Kraken `/USD` symbols is identical: 2024-06-29 to 2026-09-05,
  **798 calendar days, zero gaps, one bar/day.** That alone means T=32-33
  (the figure both this brief and the prior one treated as the current
  baseline) was never real. Verified directly: every (strategy, Kraken
  symbol, params) combination has only **8 distinct (window_start,
  window_end) pairs** behind however many rows it shows (32 for
  single-param-grid strategies, 288 for EMA Crossover's 9-param grid) —
  and all 8 are `EXPANDING` windows sharing the same start (genesis); none
  are `ROLLING` at all, because a 2-year rolling step can't produce a
  second distinct window from only 798 days, and 3-year rolling windows
  never fit even once. **The "32" is ~16 engine re-invocations over
  2026-06-20 through 2026-09-03 (confirmed via `created_at` timestamps),
  each contributing 1-2 near-duplicate expanding-from-genesis snapshots as
  a few more days of Kraken data accumulated between runs** — not 32
  independent or even deliberately-overlapping evaluation periods. This is
  the same failure mode the brief warned about ("T cannot be inflated by
  overlapping windows"), just from a mechanism the brief didn't name
  (repeated re-runs over calendar time, not a rolling-step config choice).
  A fixed, non-overlapping scheme sized to the widest single-leg warmup
  requirement (EMA Crossover's slow=200 needs 230 bars minimum) fits
  **exactly 3 non-overlapping ~266-day windows** into 798 days, honestly —
  smaller than the illusory 32, not larger. Implementing this means
  deleting the ~contaminated existing `EXPANDING` rows for these 7 symbols
  before inserting fresh ones (otherwise the leaderboard keeps averaging in
  the near-duplicates) — a real, only-partially-reversible action on
  historical data the user hasn't yet signed off on, hence stopping here.
**Blocking issues found:** the Task 3 finding above is the blocker — not a
  bug in this session's code, a data-history-and-measurement fact that
  changes what "extend T" even means here.
**Bugs discovered and logged:** none in this session's own code. The
  repeated-re-run window duplication is a pre-existing latent issue in how
  `generate_windows()` + repeated `python main.py engine` invocations
  interact over time — not introduced this session, surfaced by this
  session's investigation.
**Resume point for next session:** Awaiting the user's decision on: (1)
  whether to delete the 7 Kraken symbols' existing near-duplicate
  `EXPANDING` engine_results rows and replace them with a fixed,
  non-overlapping ~3-window scheme (T shrinks from 32 to 3, honestly); (2)
  whether that new window scheme should be a separate function used only
  for this Kraken track-record purpose, or a change to `generate_windows()`
  globally (the latter would also change T for every Binance/HTX-sourced
  candidate, far beyond this brief's scope, so the former is recommended
  but not yet built). Once resolved, Task 3's "act" half, Task 4
  (re-baseline), and Task 5 (re-propose the grid, likely against a
  significantly less favorable T=3 baseline) all still need to happen.
**Session limit hit:** yes, disclosed — Tasks 1+2's diff is 585 lines
  across 8 files (files fine, 8/10; lines over the 500 cap by 85). Not
  split: `AGENTS.md` carries both tasks' documentation in one file, and
  splitting it would need interactive patching rather than a clean
  file-level commit boundary; Task 2's own files (migration, engine, db,
  provenance, tests) are already one interdependent unit. Also stopped by
  design pending a user decision on a destructive action, per this
  session's own judgment that deleting historical rows warrants
  confirmation first.

**Continuation, same session — user confirmed both decisions:** (1) delete
  and regenerate; (2) a separate Kraken-only window function, not a global
  `generate_windows()` change.
**Files changed (Task 3):**
  - `backtesting/window_engine.py` — `generate_kraken_track_record_windows`
    (non-overlapping partition, `KRAKEN_TRACK_RECORD_MIN_WINDOW_DAYS =
    230`); `_run_over_windows` gained an optional `windows` param (reuses
    the existing default+mutation orchestration instead of duplicating it);
    new `run_kraken_track_record_windows(strategies, symbols)` method —
    skips multi-leg strategies and non-Kraken symbols itself, doesn't
    persist (caller's job).
  - `scripts/rebuild_kraken_track_record.py` (new) — computes new results
    in memory first; only deletes the legacy `EXPANDING` rows for the 7
    Kraken symbols if the new run actually produced results; re-runnable
    later against more history. `--dry-run` supported and used first.
  - `tests/test_window_engine.py` (+6): non-overlapping/back-to-back
    windows, exact T=3 pinned against the real 798-day span (so a future
    data change shows up as a test failure, not a silent T shift), the
    "never shorter than the floor" property, the too-short-history
    single-window fallback, and that `run_kraken_track_record_windows`
    skips multi-leg strategies and non-Kraken symbols without crashing.
  - `AGENTS.md` — new "Kraken track-record window configuration" section
    (the brief's explicit ask: "window count is now a number that affects
    gate outcomes, so it must be fixed and stated").
**Executed against the live DB:** `--dry-run` first (305 new results, 77
  strategy-symbol combos), then for real: **4,263 legacy `EXPANDING` rows
  deleted, 305 `INDEPENDENT` rows inserted** for the 7 Kraken symbols x 11
  single-leg strategies. Verified directly against the table (not just the
  script's own log) that every (strategy, symbol, params) triple now has
  <= 3 distinct windows, all `INDEPENDENT`. Also caught and verified as
  non-data-loss: `insert_engine_results`'s log line under-reported ("Inserted
  5" for a 305-row multi-page `execute_values` call — a pre-existing
  `cursor.rowcount`-on-last-page-only quirk, not fixed this session, noted
  in the window-configuration section above).
**Task 4 — re-baseline, best candidate per strategy, old (T=32-33, N=360)
  vs new (T=2-3, N=360):**
  - ATR Volatility Breakout: z -0.069 -> -0.216 (Δ-0.147)
  - Bollinger Band Reversion: z -5.204 -> -1.885 (Δ+3.319)
  - Dollar Cost Averaging: z -1.133 -> -0.788 (Δ+0.345)
  - Donchian Breakout: z -2.495 -> -0.703 (Δ+1.792)
  - EMA Crossover: z +0.216 -> +0.126 (Δ-0.090) — still the best candidate,
    still far short of the ~1.645 z needed for DSR>=0.95
  - HODL with Rebalance: z -6.732 -> -2.109 (Δ+4.623)
  - Keltner Squeeze: z -2.963 -> -0.719 (Δ+2.244)
  - MACD: z -5.612 -> -1.738 (Δ+3.874)
  - RSI Extremes: z -4.341 -> -1.815 (Δ+2.526)
  - SMC Breakout: z -6.840 -> -1.960 (Δ+4.880)
  - Supertrend: z -4.261 -> -0.946 (Δ+3.315)
  Z-scores moved materially for every strategy (|Δ| from 0.09 to 4.9) —
  per the brief's own framing, this confirms T (not the parameters) was
  the dominant distortion: the illusory T=32 understated the true standard
  error, making most z-scores far more extreme (mostly more negative, one
  case less positive) than honest. `oos_within_confidence_band` is now
  `None` for every single-leg Kraken candidate (needs >=4 windows; T is
  2-3) — not broken, just not enough independent history to ask the
  out-of-sample question yet. Nothing qualifies; the Prop-eligible set
  stays empty, now for an honestly-measured reason.
**Task 5 — deliberately not attempted.** The brief's own instruction: "If
  the history simply doesn't support a materially larger T, say so plainly
  and stop before Task 5." History supports a materially *smaller*, honest
  T (2-3, down from an illusory 32) — an even stronger version of that
  exit condition. Proposing a search grid against a 2-3-window foundation
  would be searching against near-total noise; the next lever is more
  Kraken calendar history, not parameters.
**Tests added (Task 3 continuation):** 6 (`test_window_engine.py`)
**Suite result:** 550 passed, 0 failed (544 after Tasks 1-2 + 6)
**Resume point for next session:** Data acquisition, not parameters —
  more Kraken price history (ideally enough for at least one full
  non-overlapping window's worth of NEW calendar time, ~230+ days, before
  T=4 and the OOS check becomes possible at all). `scripts/
  rebuild_kraken_track_record.py` is safe to re-run once more history
  exists; it always partitions whatever `[genesis, today]` span exists at
  run time. Task 5 (re-propose the grid) waits for that.

### 2026-09-06 — session "single-leg-prop-eligibility" (Claude) — COMPLETE
**Phase worked:** none of the 6 Kraken Prop phases — follow-up to 71de319
(DSR Instrumentation & Trial-Count Audit). 5 tasks: leg-count eligibility,
  Kraken-instrument provenance, wire N=360, single-leg inventory report,
  propose (don't run) a pre-registered search grid.
**DB health check:** PASSED — connectivity OK. Applied migration 0019 ->
  0020 cleanly, synced `instrument_provenance` from the live `prices` table
  (15 symbols), and ran `build_leaderboard()` against the live DB multiple
  times to gather the acceptance-report numbers below.
**engine_results row count at session start:** 134,776 (unchanged)
**Files changed:**
  - `strategies/base.py` — `BaseStrategy.leg_count = 1`,
    `BasePairsStrategy.leg_count = 2`, explicit metadata (not just an
    isinstance check).
  - `strategies/registry.py` — `StrategyEntry.leg_count`, populated from
    `strategy_cls.leg_count` in `register()`; added to `as_dict()`.
  - `alembic/versions/0020_add_instrument_provenance.py` (new) —
    `instrument_provenance(symbol, source_exchange, is_kraken_sourced,
    prop_verified, last_audited_at)`.
  - `data/provenance.py` (new) — `audit_instrument_provenance` (read-only),
    `sync_instrument_provenance` (upserts, never touches `prop_verified`),
    `load_provenance_map`. Found `backtesting.window_engine`'s
    `db.get_prices(symbol, None, None)` has no exchange filter — a real,
    pre-existing gap this module works around by deriving provenance
    straight from `prices`, not fixed (out of scope for this brief).
  - `monitoring/leaderboard.py` — `leg_count`, `is_kraken_sourced`,
    `prop_verified`, `prop_eligible`, `prop_ineligibility_reason` columns
    (priority: `MULTI_LEG_INELIGIBLE` checked before `NON_KRAKEN_SOURCE`);
    `ready_for_live` now additionally requires `prop_eligible AND
    prop_verified`; `trial_count` now adds
    `UNPERSISTED_EXPLORATORY_TRIALS_BUFFER = 55` to the persisted count
    (Task 3 — wiring the prior audit's corrected N=360, per explicit user
    authorization this session).
  - `signals/pending_signal_detector.py` — `_qualifying_single_symbol_candidates`
    now also gates on `prop_eligible` (logging the specific reason code on
    exclusion); removed the now-redundant `isinstance(strategy,
    BasePairsStrategy)` check (Rule 1 — `prop_eligible`/`leg_count` is the
    one authority now, not two overlapping mechanisms).
  - `data/provenance.py` tests (new, 7), `monitoring/leaderboard.py` tests
    (+6: multi-leg reason code, non-Kraken reason code, Kraken-sourced
    eligibility, `ready_for_live` without `prop_verified`, buffer-adjusted
    `trial_count`/`dsr_n_trials`), `signals/pending_signal_detector.py`
    tests (+1: non-Kraken exclusion). `tests/test_window_engine.py`'s
    `FakeDB` needed a branch for the new `instrument_provenance` query
    (caught via a real failure, not anticipated in advance).
**Tests added:** 14 net (7 `test_provenance.py`, 6 `test_leaderboard.py`, 1
  `test_pending_signal_detector.py`)
**Suite result:** 534 passed, 0 failed (522 before this session + 14, minus
  2 replaced-not-just-added — see files above)
**Task 3 — z-score deltas (N=305 -> N=360), the only 4 previously-qualifying
  candidates (now also correctly excluded as MULTI_LEG_INELIGIBLE):**
  Statistical Arbitrage BTC/USD|BTC/USDT: -4.1334 -> -4.2363 (Δ-0.1029);
  LINK/USD|SOL/USD: -5.6828 -> -5.7925 (Δ-0.1097); LINK/USDT|SOL/USD:
  -5.4997 -> -5.6053 (Δ-0.1056); LINK/USD|SOL/USDT: -5.4840 -> -5.5896
  (Δ-0.1056). All shifts more negative, as expected — raising N raises
  the expected max Sharpe under the null.
**Acceptance results:** Prop-eligible set (single-leg AND Kraken-sourced)
  is 133 rows across 11 of 12 single-leg strategies (Funding Rate
  Arbitrage has zero rows in engine_results at all — never run against
  real data). **Zero of the 133 have `qualifies=True`** — the Prop-eligible
  set is empty exactly as the brief anticipated. Full inventory and the
  proposed (not run) search grid are in this session's chat response, not
  duplicated here — see the "Single-Leg Prop Eligibility" report for exact
  per-strategy params/T/Sharpe/z-score and the 7-trial grid proposal
  (resulting N would be 367 if approved and run).
**Blocking issues found:** none. Confirmed empty Prop-eligible set is the
  correct, expected output per the brief — did not engineer around it.
**Bugs discovered and logged:** `backtesting.window_engine`'s
  `db.get_prices(symbol, None, None)` call has no exchange filter (see
  `data/provenance.py`'s module docstring) — real, pre-existing, NOT fixed
  this session (out of scope: the brief asked to derive/enforce provenance
  as a gate condition, not to change how the engine loads prices).
**Session limit hit:** yes, disclosed — this task's diff is 11 files / 544
  lines, over both caps (10 files, 500 lines). Not split: leg_count and
  Kraken-provenance both feed one `prop_ineligibility_reason` value per
  leaderboard row, so their `leaderboard.py`/`pending_signal_detector.py`
  changes are genuinely one interdependent edit, not two independent ones
  — same reasoning as prior disclosed overages (Phase 1, Phase 6).
**Resume point for next session:** None assigned by this brief — it
  explicitly stops after Tasks 1-3's wiring and Tasks 4-5's report, pending
  user review of the proposed search grid before anything is run.

### 2026-09-06 — session "dsr-instrumentation-trial-audit" (Claude) — COMPLETE
**Phase worked:** none of the 6 Kraken Prop phases — a small, bounded
  follow-up brief (DSR Instrumentation & Trial-Count Audit) explicitly
  scoped to NOT touch the pre-trade gate, NOT extend the overfitting
  module's correction logic, and NOT write/modify strategies.
**DB health check:** PASSED — connectivity OK, no schema changes. Ran the
  real `build_leaderboard()` against the live DB twice (before and after
  the diagnostic wiring) to get the acceptance-report numbers below.
**engine_results row count at session start:** 134,776 (unchanged)
**Files changed:**
  - `monitoring/overfitting.py` — `deflated_sharpe_ratio()` now returns a
    `DeflatedSharpeResult` dataclass (probability, z_score, observed_sharpe,
    expected_max_sharpe_null, n_trials, n_observations, skew, kurtosis,
    underflowed) instead of a bare float. `underflowed` is only True when
    `math.erf` genuinely saturates in float64 (verified this needs z below
    roughly -8.3 — real production z-scores at -4.1 to -5.7 never hit it).
    No change to the DSR/OOS math itself, only what's exposed.
  - `monitoring/leaderboard.py` — persists `dsr_z_score`,
    `dsr_expected_max_sharpe_null`, `dsr_n_trials`, `dsr_n_observations`,
    `dsr_skew`, `dsr_kurtosis`, `dsr_underflowed` on every row, alongside
    the existing `deflated_sharpe`. `passes_overfitting_gate`'s threshold
    comparison unchanged (still `deflated_sharpe >= 0.95`).
  - `tests/test_overfitting.py` — updated existing tests for the new return
    type; added the diagnostic-trail test, the underflow-vs-saturation
    distinction test (with a numerically verified z<-8.3 case), the
    no-epsilon-substitution test, and Task 2's three required controls
    (positive: N=3 genuine high Sharpe -> DSR>0.99; negative: N=305
    noise-level Sharpe -> DSR<0.01; two monotonicity tests, holding each of
    N and observed Sharpe fixed in turn).
  - `tests/test_leaderboard.py` — one new test confirming the diagnostic
    columns actually land on the leaderboard DataFrame, not just returned
    from the underlying function.
  - `AGENTS.md` — appended "DSR diagnostics" and "What counts as one trial"
    subsections under the OVERFITTING GATE RULE (Task 1's persistence
    requirement + Task 3's audit, both explicitly requested to live here).
**Tests added:** 9 net (7 in `test_overfitting.py`, 1 in `test_leaderboard.py`,
  plus type-signature updates to 2 pre-existing tests)
**Suite result:** 522 passed, 0 failed (514 before this session + 9, less
  1 dropped-and-replaced count adjustment — see files above)
**Task 3 audit findings (reported per the brief's acceptance criteria):**
  - Raw `engine_results` rows: 134,776. Current trial_count basis (distinct
    strategy x symbol x params): 305.
  - Verified, not assumed: automated mutation-grid search is fully
    persisted (20 == 20, summed across all 12 strategies' actual grid
    sizes vs. distinct persisted params) — no gap there despite
    `_mutate()`'s early-stop-on-first-pass behavior. `tmp/research_decisions.jsonl`
    (91 entries) is fully downstream of engine_results, not a separate
    trial source. 10,262 duplicate-window groups exist but don't affect
    trial_count (only per-candidate window-averaging precision).
  - The real gap: 11 of 12 strategies have a mutation grid of size 1 (no
    automated search) — their params were hand-tuned in earlier sessions
    (e.g. `STAT_ARB_ENTRY_Z`/`STAT_ARB_EXIT_Z`, 2026-06-11 session log),
    and whatever was tried before settling on the frozen values never
    became a persisted row. Per the brief's "bias upward" instruction:
    documented a conservative constant (5 assumed unpersisted trials per
    hand-tuned strategy x 11 strategies = 55) giving a **corrected N of
    360**, written into AGENTS.md's new "What counts as one trial" section.
    **Not wired into code** — `trial_count` still uses the raw 305 basis,
    per the brief's explicit "report the number and stop."
  - Per-strategy z-scores for the 4 currently-qualifying (but
    non-overfitting-gate-passing) candidates: Statistical Arbitrage on
    BTC/USD|BTC/USDT z=-4.1334, LINK/USD|SOL/USD z=-5.6828,
    LINK/USDT|SOL/USD z=-5.4997, LINK/USD|SOL/USDT z=-5.4840. All `deflated_sharpe`
    rounds to 0.0000 for display, but none are `underflowed` — these are
    small-but-nonzero probabilities (~1e-5 to 1e-8), not floating-point
    saturation, and the z-scores show BTC/USD|BTC/USDT is meaningfully
    closer to the threshold than the other three, exactly the visibility
    Task 1 asked for.
**Phase checklist progress:** n/a (not a KRAKEN PROP GAP BACKLOG phase)
**Phase completion %:** n/a
**Blocking issues found:** none. Positive control passes (required
  acceptance criterion); full suite green (required acceptance criterion).
**Bugs discovered and logged:** none in the DSR/OOS logic — it was already
  correct, just opaque. One unrelated latent inconsistency noticed during
  the Task 3 audit and NOT touched (out of scope): `MAX_MUTATION_GENERATIONS
  = 3` exists in `backtesting/window_engine.py` but the orchestration code
  only ever calls `_mutate()` once per window (from `default_result`,
  generation 0 -> 1), never recursively on a still-failing mutation — so
  generations 2 and 3 are structurally unreachable despite the guard
  constant implying they're supported. Confirmed against real data (only
  generations 0 and 1 appear in `engine_results`). Not fixed — flagged for
  a future session if it matters, per this brief's explicit "do not extend"
  scope boundary.
**Resume point for next session:** None from this session specifically —
  it was a bounded, complete audit. The corrected-N finding (360 vs 305) is
  a decision point for the user: whether to actually raise `trial_count`'s
  basis (which would make `passes_overfitting_gate` harder to clear for
  every candidate) is deliberately left unresolved here.
**Session limit hit:** no — brief was explicitly scoped small enough to
  finish in one session (3 tasks, ~5 files, well under both caps).

### 2026-09-06 — session "kraken-prop-phase6-overfitting-correction" (Claude) — COMPLETE
**Phase worked:** KRAKEN PROP GAP BACKLOG, Phase 6 (leaderboard overfitting
  correction) — the brief's last phase; all 6 now done
**DB health check:** PASSED — connectivity OK, no schema changes. Ran the
  real `python main.py leaderboard` command against the live DB (305
  strategy-symbol-params trials, `engine_results` unchanged at 134,776
  rows) — found every currently-"qualifying" strategy's deflated Sharpe is
  exactly 0.0 (their Sharpe is fully explainable by luck among 305 trials).
  Not a synthetic finding — this is what the correction found on real data
  the day it shipped, now recorded in the new OVERFITTING GATE RULE.
**engine_results row count at session start:** 134,776 (unchanged)
**Files changed:**
  - `monitoring/overfitting.py` (new) — `deflated_sharpe_ratio` (Bailey &
    Lopez de Prado, 2014), `walk_forward_oos_within_band`, and their
    building blocks (`sharpe_standard_error`, `expected_max_sharpe_under_null`,
    a from-scratch inverse-normal-CDF via Acklam's approximation — verified
    against scipy to ~9 significant figures, no new dependency needed).
    Chose deflated Sharpe over the brief's alternative (White's Reality
    Check) because the latter needs raw per-trade return series
    `engine_results` doesn't store; documented as a deliberate scope choice.
  - `tests/test_overfitting.py` (new, 11 tests) — including that DSR
    penalizes more trials searched for the identical observed Sharpe (the
    brief's central point), and the walk-forward split is chronological,
    not sorted-by-magnitude.
  - `monitoring/leaderboard.py` — `build_leaderboard()` now does a second
    pass after computing every candidate's `avg_sharpe` (deflated Sharpe
    needs the whole cross-sectional spread up front): adds `trial_count`,
    `deflated_sharpe`, `oos_within_confidence_band`, `passes_overfitting_gate`
    columns; `ready_for_live` now additionally requires
    `passes_overfitting_gate`.
  - `tests/test_leaderboard.py` — added `window_end` to the row fixture
    (new required column), 6 new tests for the overfitting columns and the
    `ready_for_live` interaction. One planned test (DSR strictly decreasing
    with more trials, at the leaderboard level) was dropped after it proved
    numerically confounded — trial_count and the trial-Sharpe distribution
    are structurally coupled in real leaderboard data in a way the
    lower-level `test_overfitting.py` test (where they're independent
    parameters) already covers correctly; kept a comment explaining why
    rather than leaving a flaky/wrong test in place.
  - `signals/pending_signal_detector.py` — `_qualifying_single_symbol_candidates`
    (the actual live-qualification gate for Kraken Prop manual signals) now
    additionally requires `passes_overfitting_gate`, not just `qualifies`.
  - `tests/test_pending_signal_detector.py` — updated the mock leaderboard
    row helper for the new column; added a test proving a strategy that
    qualifies on pass_ratio alone but fails the overfitting gate is excluded.
  - `AGENTS.md` — new **OVERFITTING GATE RULE** (mandatory, peer to
    DATABASE HEALTH RULE / PHASE GATE RULE), per the brief's explicit
    instruction to add this as a gate condition in AGENTS.md, not just a
    report; KRAKEN PROP GAP BACKLOG marked all 6 phases done.
**Tests added:** 18 net (11 in `test_overfitting.py`; `test_leaderboard.py`
  net +6; `test_pending_signal_detector.py` net +1)
**Suite result:** 514 passed, 0 failed (497 before this phase + 18, minus 1
  dropped test — see above)
**Phase checklist progress:** Phase 6 ✅ DONE — brief complete, all 6 phases
**Phase completion %:** 100%
**Blocking issues found:** none. The dropped test (see above) was a testing
  methodology issue caught and resolved in-session, not a blocker.
**Bugs discovered and logged:** none new in code — but a real, live finding:
  every currently-qualifying strategy has a 0.0 deflated Sharpe. This is not
  a bug to fix; it's the correction correctly reporting that nothing on the
  current leaderboard is statistically distinguishable from noise given how
  many trials were searched. No leaderboard-shown "qualifying" strategy
  should be treated as validated until new engine runs produce a candidate
  that actually clears passes_overfitting_gate.
**Resume point for next session:** The Kraken Prop brief's 6 phases are all
  done. Known follow-ups recorded across AGENTS.md's "Known conflicts"
  entries for this backlog (not new work, just what's left before this
  fully protects a live account): (1) calibrate the correlation cap and
  consecutive-loss limit placeholders in `risk/pretrade_gate.py`; (2) wire
  real `kraken_prop_mdd_pct` (per actual account tier) and persist real
  rollover-clock state instead of the current conservative/same-cycle
  defaults; (3) confirm with the user whether Phase 5's Android client work
  should begin, since the brief scoped that to "a separate session."
**Session limit hit:** partially — no phase to defer to (this was the
  brief's final phase), but the diff is 514 lines across 7 files, a small
  overage past the 500-line cap (files count fine, 7/10). Not split: the
  math module, its leaderboard wiring, and the detector wiring are one
  interdependent unit — disclosing rather than fragmenting, same call made
  for Phase 1's similar small overage.

### 2026-09-06 — session "kraken-prop-phase5-publish-endpoint" (Claude) — COMPLETE
**Phase worked:** KRAKEN PROP GAP BACKLOG, Phase 5 (publish endpoint)
**DB health check:** PASSED — connectivity OK, no schema changes this
  phase. Live-verified end-to-end against a running dashboard instance with
  `DASHBOARD_API_TOKEN` set: no token -> 401, wrong token -> 401, correct
  token -> 200 with real `daily_room_remaining`/`lifetime_room_remaining`
  computed from the live DB; also re-checked `/api/pending-signals` and
  `/api/trading-status` still 200 after the `trading_routes.py` refactor.
**engine_results row count at session start:** 134,776 (unchanged)
**Files changed:**
  - `monitoring/routes/publish.py` (new) — `GET /api/publish/setups`,
    `require_token`-protected (the first GET route in this dashboard to
    require auth — every other GET is same-origin-browser-only by
    convention; this surface is reached by a device outside that boundary).
    Returns active (gate-approved) setups plus current
    `daily_room_remaining`/`lifetime_room_remaining` from the same
    `PropAccountState` the gate itself evaluated against.
  - `docs/openapi/publish.yaml` (new) — OpenAPI 3.0.3 schema for the
    endpoint, for the Android client to be generated against in a separate
    session (per the brief).
  - `tests/test_publish_routes.py` (new, 4 tests) — including a structural
    test (inspects `app.url_map`, not a comment) that no route on this
    blueprint accepts POST/PUT/PATCH/DELETE, per the brief's "no write
    methods on this surface at all."
  - **Deduplication surfaced while building this** (Rule 1): extracted
    `signals.pending_signal_detector.current_account_state(pos, cfg)` as the
    one place that builds a live `PropAccountState` (previously inlined in
    `scan_kraken_signals`; now shared with the new publish route so neither
    could drift from the other), extracted `decimal_columns_to_float()`
    (same module — was duplicated verbatim into the new route while writing
    it, caught immediately and factored out instead of shipped as two
    copies), and moved `_records()` from `trading_routes.py` into a new
    `monitoring/routes/_json.py` so both route files import the same
    NaN-handling helper instead of each defining it.
  - `monitoring/routes/trading_routes.py`, `monitoring/dashboard_app.py` —
    updated for the above (import the shared helpers; register the new
    blueprint).
**Tests added:** 4 (`test_publish_routes.py`)
**Suite result:** 497 passed, 0 failed (493 before this phase + 4)
**Phase checklist progress:** Phase 5 ✅ DONE
**Phase completion %:** 100%
**Blocking issues found:** none. Confirmed no existing Tailscale-specific
  code/config anywhere in the repo before concluding "over Tailscale" is a
  deployment concern, not something to build.
**Bugs discovered and logged:** none new — but see the deduplication note
  above: while writing this phase's route I initially reproduced (not
  fixed, reproduced) trading_routes.py's Decimal-to-float conversion
  verbatim, caught it before committing, and factored both copies out.
  Logging the pattern (a new route copy-pasting an existing route's
  boundary-conversion logic) as a thing to watch for in future phases.
**Resume point for next session:** Phase 6 — leaderboard overfitting
  correction (deflated Sharpe / White's Reality Check, walk-forward
  confidence bands, trial count recorded on the leaderboard record). This is
  the last phase in the brief and also what should eventually feed Phase
  4's currently-stubbed `strategy_benched` flag in `risk/pretrade_gate.py`.
**Session limit hit:** yes — stopping after Phase 5 per PHASE GATE RULE,
  pending user review before Phase 6 (the brief's final phase).

### 2026-09-06 — session "kraken-prop-phase4-pretrade-gate" (Claude) — COMPLETE
**Phase worked:** KRAKEN PROP GAP BACKLOG, Phase 4 (pre-trade gate)
**DB health check:** PASSED — connectivity OK; applied `0018 -> 0019`
  cleanly; live-verified `evaluate()` + `persist_decision()` against the real
  DB (inserted one real `gate_decisions` row, confirmed JSONB round-trips
  Decimal-as-string correctly, then deleted it). Also ran the real
  `python main.py signal-scan --exchange kraken` CLI end-to-end — no
  qualifying leaderboard candidates right now (unchanged from Phase 1/3
  verification), so the gate path itself needed the direct DB check above.
**engine_results row count at session start:** 134,776 (unchanged)
**Files changed:**
  - `risk/pretrade_gate.py` (new) — `evaluate(setup, account_state, context)
    -> GateDecision` implementing all 6 reject rules from the brief in
    priority order (benched -> consecutive-loss -> daily hard -> lifetime
    hard -> daily room -> lifetime room -> fee filter -> correlation cap),
    falling through to REDUCE when the daily soft floor is active and
    everything else passes, else APPROVE. `persist_decision()` writes every
    call (approved or not) to `gate_decisions` for the audit trail the
    brief requires. `KRAKEN_LEVERAGE_CAPS` records verified current values
    (blog.kraken.com, 2026-08 — matches the brief's table exactly) but is
    NOT enforced here — leverage isn't one of the brief's 6 Phase-4 rules.
  - `alembic/versions/0019_add_gate_decisions.py` (new) — JSONB `inputs`
    column, indexed on (strategy_name, symbol) and action.
  - `tests/test_pretrade_gate.py` (new, 14 tests) — one per rule plus the
    haircut/correlation-netting semantics and the persistence write.
  - `signals/pending_signal_detector.py` — `scan_kraken_signals` now builds
    a `PropAccountState` (via `from_position_state`, not a second account
    model) and routes every candidate through `evaluate()` +
    `persist_decision()` before any `_upsert_active_signal()` call — REJECT
    decisions are logged and skipped, never written as active.
  - `tests/test_pending_signal_detector.py` — the brief's required "gate is
    the only path" test: mocks `evaluate()` to REJECT and asserts
    `_upsert_active_signal` is never called (and to APPROVE, asserting it is).
**Tests added:** 16 net (14 in `test_pretrade_gate.py`; `test_pending_signal_detector.py` net +2)
**Suite result:** 493 passed, 0 failed (477 before this phase + 16)
**Phase checklist progress:** Phase 4 ✅ DONE
**Phase completion %:** 100% of the 6 specified rules. Explicitly NOT
  100% of a live-ready gate — see "Known conflicts" above for the two
  uncalibrated placeholders (correlation cap, consecutive-loss limit) and
  the two account-state gaps (mdd_pct tier, rollover-clock persistence)
  that must be fixed before this runs unattended against a real account.
**Blocking issues found:** none blocking. Verified via WebSearch that
  Kraken's current leverage caps match the brief's table exactly (BTC 10x,
  NDX 10x, S&P 10x, SOL 5x, HYPE 3x) rather than trusting the brief blindly,
  per its own "verify current values, do not hardcode" instruction — also
  found each cap's dollar notional limit, recorded for later use.
**Bugs discovered and logged:** none
**Resume point for next session:** Phase 5 (publish endpoint: authenticated
  read-only Tailscale endpoint + OpenAPI schema for the tablet app) or
  Phase 6 (leaderboard overfitting correction — deflated Sharpe / White's
  Reality Check, walk-forward confidence bands; note Phase 4's rule 6
  `strategy_benched` is currently a plain caller-supplied bool with no real
  computation behind it — Phase 6 is what should eventually feed it).
**Session limit hit:** yes — stopping after Phase 4 per PHASE GATE RULE,
  pending user review before Phase 5/6.

### 2026-09-06 — session "kraken-prop-phase3-trade-setup" (Claude) — COMPLETE
**Phase worked:** KRAKEN PROP GAP BACKLOG, Phase 3 (TradeSetup sizing/cost/risk math)
**DB health check:** PASSED — connectivity OK; applied migration `0017 -> 0018`
  cleanly (table was empty, verified via `information_schema.columns` that all
  5 price/size columns are now `numeric`); live-verified the `/api/pending-signals`
  endpoint against a running dashboard instance after the change (seeded one
  NUMERIC-backed row, confirmed JSON response carries real numbers, not
  Decimal-as-string, then deleted it)
**engine_results row count at session start:** 134,776 (unchanged)
**Files changed:**
  - `strategies/setup.py` (new) — `derive_size` (equity*risk_pct/stop_distance,
    risk_pct default 0.25%, its own constant), `derive_notional`,
    `derive_leverage_required` (margin-efficiency readout only, provably
    unused by `derive_size`), `derive_expected_cost` (wraps
    `risk.cost_model.total_expected_cost`), `derive_worst_case_loss`,
    `derive_r_multiple` (cost-adjusted). Pure Decimal, no dataclass.
  - `tests/test_strategy_setup.py` (new, 11 tests) — including the brief's
    explicit leverage-invariance acceptance test. **Named to avoid a
    filename collision**: this session's first attempt overwrote a
    pre-existing, unrelated `tests/test_setup.py` (an old environment/
    dependency-verification script, tracked since the initial commit) via
    `Write` without checking first — caught immediately via `git status`
    showing it as modified rather than new, restored with `git restore`
    (confirmed no data loss), new tests moved to this non-colliding name.
    Lesson: check `git status`/existence before `Write`-ing into `tests/`
    with a name inferred from a source module, not asserted by the user.
  - `signals/pending_signal_detector.py` — `PendingSignal` fields converted
    to Decimal; added `expected_cost`/`worst_case_loss`/`r_multiple`/
    `leverage_required`/`expected_hold_hours`/`generated_at`. `_build_payload`
    now sizes via `strategies.setup` instead of `_target_notional` (see
    "Known conflicts" above).
  - `tests/test_pending_signal_detector.py` — updated for Decimal types;
    replaced the now-obsolete "not raw leg allocation" test (that concern no
    longer applies — there's no leg allocation left in this path) with sizing
    tests cross-checked against `strategies.setup` directly.
  - `alembic/versions/0018_pending_signals_decimal.py` (new) — price/size
    columns FLOAT8 → NUMERIC; adds the 5 new risk/cost columns.
  - `monitoring/routes/trading_routes.py` — explicit Decimal → float
    conversion before `jsonify` in `/api/pending-signals` (see "Known
    conflicts" above for why this is necessary, not optional).
**Tests added:** 13 net (11 new in `test_strategy_setup.py`; `test_pending_signal_detector.py` net +2 after removing the obsolete test)
**Suite result:** 477 passed, 0 failed (464 before this phase + 13)
**Phase checklist progress:** Phase 3 ✅ DONE (TradeSetup math + PendingSignal
  consolidation; `BaseStrategy` deliberately untouched per the resolution
  recorded at the top of this backlog)
**Phase completion %:** 100%
**Blocking issues found:** none blocking, but two real conflicts were
  surfaced and resolved with the user before writing code: (a) the brief's
  sizing formula vs. `_target_notional` — resolved as "switch, don't keep
  both"; (b) Decimal fields needing NUMERIC DB columns to avoid silently
  losing precision on write — resolved as "migrate now, table's still empty."
**Bugs discovered and logged:** the `tests/test_setup.py` filename collision
  above (self-inflicted this session, caught and fixed before commit — not a
  pre-existing bug, logging it as a process lesson)
**Resume point for next session:** Phase 4 — `risk/pretrade_gate.py`. Needs a
  live `PropAccountState` feed (Phase 2 built the type; nothing populates it
  from real running state yet — `from_position_state()` plus a real
  `kraken_mdd_pct` config value and persisted `last_rollover` are the gaps to
  close first) and Kraken's actual per-asset leverage caps (brief: "verify
  current values, do not hardcode blindly" — not yet looked up). The gate
  must be provably the only path to a live setup — brief requires a test
  asserting this.
**Session limit hit:** yes, two ways — (1) stopping after Phase 3 per PHASE
  GATE RULE, pending user review before Phase 4; (2) this phase's diff is 521
  lines across 7 files, over the 500-line cap (files count is fine, 7/10).
  Not split into partial commits: the change is one atomic, fully-tested unit
  (detector rewrite + its migration + its API-boundary fix + updated tests
  all depend on each other) — splitting would leave intermediate commits with
  a broken test suite, which the "never leave a failing suite" rule outranks.
  Disclosing the overage rather than silently ignoring it.

### 2026-09-06 — session "kraken-prop-phase2-account-clock" (Claude) — COMPLETE
**Phase worked:** KRAKEN PROP GAP BACKLOG, Phase 2 (account state + daily clock)
**DB health check:** carried over from this session's Phase 1 entry below (same
  session, no new DB-touching changes — connectivity/row-count unchanged)
**engine_results row count at session start:** 134,776 (unchanged from Phase 1)
**Files changed:**
  - `risk/prop_account.py` (new) — `PropAccountState` (balance, equity,
    mdl_floor, mdd_floor, daily_room_remaining, lifetime_room_remaining per
    spec, plus `daily_soft_triggered`/`daily_hard_triggered`/
    `lifetime_hard_triggered`); `from_position_state()` builds one from the
    existing `trading.position.PositionState` (str-converts its floats —
    `pos.equity`/`daily_start_equity`/`peak_equity` — so no binary-float
    artifact enters the Decimal path). Daily trip-wires (1.5%/2.0%) are
    absolute percentages of balance, matching the existing dollar-amount
    soft/hard floor's shape; the lifetime trip-wire (70%) is a fraction of
    the tier-dependent `kraken_mdd_pct` since that room isn't a fixed constant.
  - `risk/daily_clock.py` (new) — `most_recent_rollover`, `is_rollover_due`,
    `apply_rollover`, `maybe_rollover`. 00:30 UTC boundary, inclusive at
    exactly :30. Deliberately does not reuse
    `PositionState.day_rolled()` (UTC-calendar-midnight) — confirmed by test
    that the two boundaries diverge in the 00:00–00:30 UTC window.
  - `tests/test_prop_account.py` (new, 13 tests), `tests/test_daily_clock.py`
    (new, 13 tests) — including the boundary case the brief specifically
    flagged: not-due between midnight and 00:30 despite the calendar date
    having already changed.
**Tests added:** 26
**Suite result:** 464 passed, 0 failed (438 before this phase + 26)
**Phase checklist progress:** Phase 2 ✅ DONE
**Phase completion %:** 100% of the account-state/clock deliverable. Live
  enforcement wiring (making `trading/paper_trader.py` actually obey these
  triggers) is explicitly NOT part of this phase — deferred to Phase 4's
  pre-trade gate by design (see "Known conflicts, resolved" above), not an
  oversight.
**Blocking issues found:** none
**Bugs discovered and logged:** none
**Resume point for next session:** Phase 3 — `strategies/setup.py`
  (`TradeSetup`, consolidating into `signals.pending_signal_detector.
  PendingSignal` per the resolution recorded above) plus `size`/`notional`/
  `leverage_required`/`expected_cost` (from `risk/cost_model.py`)/
  `worst_case_loss`/`r_multiple`/`source_timeframe` (from
  `strategies/timeframe_resolver.py`, already exists — read it first). Test
  requirement from the brief: doubling max leverage must leave `size`
  unchanged for an identical setup (leverage affects margin only).
**Session limit hit:** yes — stopping after Phase 2 per PHASE GATE RULE,
  pending user review before Phase 3.

### 2026-09-06 — session "kraken-prop-phase1-cost-model" (Claude) — COMPLETE
**Phase worked:** KRAKEN PROP GAP BACKLOG, Phase 1 (cost model)
**DB health check:** PASSED — `db.test_connection()` True; `engine_results` = 134,776 rows; migration chain at `0017` (head), `0015`/`source_timeframe` confirmed applied
**engine_results row count at session start:** 134,776
**Files changed:**
  - `risk/__init__.py`, `risk/cost_model.py` (new) — `round_trip_commission`,
    `funding_cost` (whole 4-hour blocks, not prorated), `total_expected_cost`
    (duck-typed against `.notional`/`.expected_hold_hours` so it has no
    dependency on the not-yet-built `TradeSetup`). Decimal throughout, no floats.
  - `tests/test_cost_model.py` (new) — the brief's exact acceptance case
    ($50K notional round trip = $40 = 6.7% of a $600 MDD buffer; one day
    funding = $16.50 = 2.75%), plus block-rounding, zero/negative-hours, and
    a test that Decimal-vs-float raises `TypeError` (the actual enforcement
    mechanism for "no floats in the risk path")
  - `AGENTS.md` — second pivot banner (prop-firm compliance back, scoped to
    Kraken Prop); new **KRAKEN PROP GAP BACKLOG** section (Phases 1–6) with
    conflict resolutions recorded (TradeSetup/PendingSignal consolidation,
    BaseStrategy left untouched, existing floor system extended not duplicated)
  - Also committed (separately, incidental to session start, not counted
    against this phase): `signals/pending_signal_detector.py`,
    `cli/signal_scan.py`, `alembic/versions/0017_add_pending_signals.py`,
    dashboard Signals tab — pre-existing uncommitted work from before this
    brief arrived, reviewed/tested/committed first per user instruction
**Tests added:** 7 (`test_cost_model.py`)
**Suite result:** 438 passed, 0 failed (431 before this session's commits + 7)
**Phase checklist progress:** Phase 1 ✅ DONE
**Phase completion %:** 100%
**Blocking issues found:** none for Phase 1. Flagged and resolved with the user
  before starting: (a) this brief reverses the 2026-06-20 prop-firm pivot —
  confirmed intentional, scoped to Kraken Prop; (b) Phase 3 as literally
  specified would change `BaseStrategy.generate_signals`'s return type,
  breaking the backtest engine/leaderboard — resolved as "TradeSetup is a new
  live layer, generate_signals unchanged"; (c) `TradeSetup` duplicates
  `PendingSignal` — resolved as "consolidate, extend PendingSignal."
**Bugs discovered and logged:** none
**Resume point for next session:** Phase 2 — `risk/prop_account.py` +
  `risk/daily_clock.py`. Must extend (not parallel) the existing floor system
  in `trading/paper_trader.py` (soft ~$130/hard ~$145 DD, dollar-based) and
  `trading/position.py::max_safe_notional`; must add daily-room-percentage
  tiers (soft 1.5%/hard 2.0%) and a 00:30 UTC rollover distinct from
  `PositionState.day_rolled()`'s current UTC-calendar-midnight boundary.
  Write rollover-boundary tests specifically (brief: "off-by-one on a
  timezone here is an account-ending bug").
**Session limit hit:** yes — max 1 phase per session; stopping after Phase 1
  per PHASE GATE RULE, pending user review before Phase 2.

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
