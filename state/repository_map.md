# Repository Map — RAFund ML4T

**Purpose:** Phase 0 deliverable for [AGENT_ORCHESTRATION.md](../AGENT_ORCHESTRATION.md) (Section 48/65). Ground truth as read from the actual code on 2026-09-30, branch `feature/agent-orchestration` — not summarized from README or from memory. Every claim below cites the file it came from; where something is a placeholder or unwired, that is called out explicitly rather than glossed over.

---

## 1. Architecture, in one paragraph

Strategies (`strategies/`) emit a per-bar `pd.Series` of BUY/SELL/HOLD from `BaseStrategy.generate_signals` — this contract is fixed and never changes for the Senior Trader work. `backtesting/window_engine.py` walk-forward-tests every strategy × symbol combination and writes `engine_results` rows. `monitoring/leaderboard.py::build_leaderboard()` aggregates those rows into a ranked table with a DSR-corrected overfitting gate. `signals/pending_signal_detector.py::scan_kraken_signals()` reads that leaderboard, builds a fully-sized, fully-costed `PendingSignal` for each qualifying Kraken symbol, and routes every one of them through `risk/pretrade_gate.py::evaluate()` — the sole authority that can mark anything APPROVE/REDUCE/REJECT. Approved/reduced setups are upserted into `pending_signals` (status='active'); `monitoring/routes/publish.py` exposes them read-only to a tablet. **No code anywhere places a Kraken Prop order** — a human reads the tablet and executes by hand in Kraken's own app. This is exactly the gap Agent Orchestration is meant to fill with judgment, not to replace.

A structurally separate path — `trading/live_trader.py` + `trading/execution.py` (CCXT) — does place real orders, but only on Binance/Kraken-spot/HTX, never against Kraken Prop (which has no order API at all). Do not conflate the two.

---

## 2. Signal-scan trace (exactly what the spec asked to trace)

`python main.py signal-scan --exchange kraken` → `cli/signal_scan.py::run_signal_scan_cmd()`:

1. `expire_stale_signals(db, exchange)` — marks `pending_signals` rows expired by TTL or by price drift > `pending_signal_stale_price_move_pct` (default 1%, code constant — see §5).
2. `scan_kraken_signals(db, exchange)` (`signals/pending_signal_detector.py:299`):
   - Loads `PositionState` for `run_id="live_kraken"`. If `account_failed`, `daily_halt`, or `manual_halt` → returns `[]` immediately, no gate calls at all.
   - `_qualifying_single_symbol_candidates(db)` → calls `build_leaderboard(db)`, filters to rows where `qualifies AND passes_overfitting_gate AND prop_eligible` (best one row per (strategy, symbol) by rank). **Does not** require `prop_verified` — that flag only affects the separate `ready_for_live` column, not scan eligibility.
   - Builds `current_account_state(pos, cfg)` → a `risk.prop_account.PropAccountState`.
   - For each candidate: pulls latest bars, gets `_last_signal`, resolves stop via `strategy.get_stop_level()` (only `SMCBreakout` implements this; everything else falls back to a flat 2% stop), then `_build_payload()` computes a full `PendingSignal` — sizing via `strategies/setup.py::derive_size` (risk-based, `equity × 0.25% / stop_distance`, never a function of leverage).
   - **Every** candidate (not just eventual approvals) goes through `risk.pretrade_gate.evaluate(payload, account_state, GateContext())`, and `persist_decision()` writes it to `gate_decisions` regardless of outcome.
   - Only `APPROVE`/`REDUCE` outcomes get `_upsert_active_signal()` into `pending_signals`. `REJECT` is logged and dropped — never reaches `status='active'`.

This is the entire "signal → decision" pipeline today. There is no step after this that executes, journals a fill, or reconciles reality — that's the exact gap Section 1 of AGENT_ORCHESTRATION.md describes as the Senior Trader's job.

---

## 3. `risk/pretrade_gate.py::evaluate()` — the six ordered checks

Source: `risk/pretrade_gate.py:119-181`. In order, first match wins:

1. `strategy_benched` → REJECT `strategy_benched` (currently always `False` — nothing sets this; see §5)
2. `consecutive_losses_today >= consecutive_loss_limit` (default limit **3**, uncalibrated placeholder, always `0` today — nothing tracks this yet) → REJECT `consecutive_loss_breaker`
3. `account_state.daily_hard_triggered` → REJECT `daily_hard_floor`
4. `account_state.lifetime_hard_triggered` → REJECT `lifetime_hard_floor`
5. `setup.worst_case_loss > _effective_daily_room(account)` (halved if soft floor already tripped) → REJECT `daily_room_insufficient`
6. `setup.worst_case_loss > _effective_lifetime_room(account)` → REJECT `lifetime_room_insufficient`
7. `expected_cost > 15% of R` → REJECT `fee_filter`
8. `abs(net_directional_exposure) > equity × correlation_cap_pct` (default cap **100% of equity**, uncalibrated placeholder) → REJECT `correlation_cap`
9. `daily_soft_triggered` → REDUCE `daily_soft_floor_active`
10. else → APPROVE `all_checks_passed`

Two of these ship with **admitted placeholder numbers the code's own docstring says must not be trusted** (correlation cap 100% of equity, consecutive-loss limit 3) — a real risk owner has never calibrated them. Agent Orchestration must treat these exactly as the spec's Section 3 demands: never reinterpret, never override, but also never present a REJECT/APPROVE from an uncalibrated rule as more authoritative than it is when explaining a decision to a human.

`GateDecision` is `{action, reason_code, details}` — a plain dataclass, not persisted directly; `persist_decision()` is the only writer to `gate_decisions` (schema: `strategy_name, symbol, direction, action, reason_code, inputs JSONB, created_at` — migration `0019`).

---

## 4. DSR / overfitting eligibility — exactly how "eligible" is computed

Source: `monitoring/overfitting.py` + `monitoring/leaderboard.py:305-352`.

- `monitoring/overfitting.py::deflated_sharpe_ratio()` implements Bailey & Lopez de Prado (2014) DSR with **Gaussian skew=0/kurtosis=3 assumed, not measured** (documented simplification — `engine_results` stores per-window summaries, not raw per-trade returns) and no scipy dependency (hand-rolled inverse-normal via Acklam's approximation).
- `walk_forward_oos_within_band()` — chronological split-half check, needs ≥4 windows or returns `None` (treated as **not passing**, never as passing-by-default).
- `build_leaderboard()` computes, per (strategy, symbol): `passes_overfitting_gate = dsr_probability >= min_deflated_sharpe (default 0.95) AND oos_ok is True`. `trial_count = len(records) + UNPERSISTED_EXPLORATORY_TRIALS_BUFFER` (documented in AGENTS.md as 305 persisted + 55 buffer = 360).
- Separately: `prop_eligible = leg_count == 1 AND is_kraken_sourced` (from the `instrument_provenance` table, migration `0020`, populated by an offline audit script — not automatic).
- `ready_for_live` (strictest tier, gates the dashboard's "live-ready" badge, **not** scan eligibility) additionally requires `prop_verified` — a hand-set flag with no automated source ("the user populates it by hand from the Prop market selector," per migration `0020`'s docstring).
- **The bar `scan_kraken_signals` actually uses is `qualifies AND passes_overfitting_gate AND prop_eligible`** — three of these four gates, not all four. `prop_verified`/`ready_for_live` is stricter and currently gates nothing in the live signal path.

As of the last README sync (`da40345`), **zero strategies clear the DSR bar** — this needs to be re-verified live (`python main.py leaderboard`), not assumed to still be zero, before Agent Orchestration reports it as current state.

---

## 5. Account/risk state — what's real vs. placeholder

`risk/prop_account.py::PropAccountState` (frozen dataclass, Decimal-exact): `balance, unrealized_pnl, accrued_funding_cost, daily_start_balance, peak_balance, kraken_mdd_pct, last_rollover`, with computed properties `equity, mdl_floor, mdd_floor, daily_room_remaining, lifetime_room_remaining, daily_loss_pct, lifetime_drawdown_fraction, daily_soft_triggered, daily_hard_triggered, lifetime_hard_triggered`.

Built via `from_position_state()` from the existing `trading.position.PositionState` (float-based, DB-backed via `paper_positions`) — **not** a second, independently-fed account model. The single authority for building this at request time is `signals.pending_signal_detector.current_account_state(pos, cfg)`, shared by both the gate and the tablet publish endpoint so they can never drift.

**Not yet real, called out in the code itself:**
- `kraken_prop_mdd_pct` defaults to a hardcoded 3% (conservative end of Kraken's 3-6% tier range) — not read from any per-account config today (`config/loader.py`'s `Settings` class has no `kraken_prop_mdd_pct` field; it's accessed via `getattr(cfg, "kraken_prop_mdd_pct", _DEFAULT_KRAKEN_MDD_PCT)`, i.e. always falls through to the default).
- `last_rollover` is set to `datetime.now(timezone.utc)` on every call — a same-cycle placeholder. `risk/daily_clock.py` (the 00:30 UTC rollover logic) **exists as a pure function but is not wired to run on any schedule** — nothing persists real rollover state.
- `unrealized_pnl` / `accrued_funding_cost` are passed as `Decimal("0")` by default at every call site seen — no live feed populates them.
- `strategy_benched` and `consecutive_losses_today` in `GateContext` are always their defaults (`False`, `0`) — `scan_kraken_signals` constructs a bare `GateContext()` with no tracking behind either field.

**This list is the actual, current "what a Senior Trader must not pretend is live" set** — Section 41 of AGENT_ORCHESTRATION.md's "must never invent" rule applies directly here.

---

## 6. Order / journal structures already in the database

Two, deliberately separate, tables — do not conflate them:

| Table | Migration | Written by | Meaning |
|---|---|---|---|
| `pending_signals` | 0017, decimalized in 0018 | `signals/pending_signal_detector.py::_upsert_active_signal` | A *proposal* — append/refresh only, one active row per (strategy, symbol, direction) via a partial unique index. Never a record of an actual fill. |
| `gate_decisions` | 0019 | `risk/pretrade_gate.py::persist_decision` | Append-only audit of **every** `evaluate()` call — inputs + decision, whether APPROVE/REDUCE/REJECT. |
| `paper_orders` | 0009 (journal columns) | `trading/position.py::log_order` | Actual (paper or CCXT-live) fills — `event, side, qty, price, pnl, commission, order_type, exchange_order_id, setup_tag, close_reason, source_timeframe`. This is the closest existing analogue to Section 21's journal spec, but it has **no field for a manually-executed Kraken Prop fill** — there is no `execution_mode`, no browser-verification status, no `decision_id` linking back to a `pending_signals` row. |
| `instrument_provenance` | 0020 | offline audit script, hand-set `prop_verified` | Per-symbol Kraken-sourced / Prop-verified flags feeding leaderboard eligibility. |

**Gap for Agent Orchestration:** a manually-placed Kraken Prop trade today has nowhere to be journaled — `paper_orders` assumes an automated `PositionState`-driven fill, and `pending_signals` only tracks the proposal, not what a human actually did with it. Section 21/22's journal schema (`decision_id`, `gate_decision`, `gate_snapshot`, `trader_decision`, `execution_status`, `divergence_flags`) does not exist anywhere yet — this is new schema, not a reuse of an existing table.

---

## 7. Execution layer that already exists (do not duplicate)

`trading/execution.py` defines `ExecutionOrder`, `ExecutionAdapter` (ABC), `PaperExecutionAdapter`, `CCXTExecutionAdapter`, `ExecutionEngine`, and `build_execution_engine()`. This is consumed **only** by `trading/live_trader.py` (CCXT limit orders against Binance/Kraken-spot/HTX — the non-Prop path). It has no browser automation, no Playwright, and is architecturally unrelated to Kraken Prop (which this adapter cannot reach — Prop has no API for `CCXTExecutionAdapter` to call).

Do not route Kraken Prop browser execution through this module. AGENT_ORCHESTRATION.md's proposed `execution/` tree (compliance gate, browser operator, execution intent) is genuinely new — `trading/execution.py` solves a different, already-closed problem.

---

## 8. Strategy contract

`strategies/base.py::BaseStrategy.generate_signals(df, params) -> pd.Series` (BUY/SELL/HOLD per bar) — untouched by anything Kraken-Prop-related, per AGENTS.md's explicit binding decision (§ KRAKEN PROP GAP BACKLOG). `get_stop_level(df, params)` defaults to `None`; only `SMCBreakout` overrides it. `strategies/registry.py::StrategyRegistry` is the decorator-based lookup (`@StrategyRegistry.register`, `.instantiate(name)`, `.get(name).leg_count`) used everywhere a strategy is looked up by name, including `pending_signal_detector.py`.

`strategies/setup.py` — pure Decimal sizing formulas (`derive_size`, `derive_notional`, `derive_leverage_required`, `derive_expected_cost`, `derive_worst_case_loss`, `derive_r_multiple`), deliberately **not** a second dataclass alongside `PendingSignal` (AGENTS.md Rule 1). `DEFAULT_RISK_PCT = 0.25%`, intentionally distinct from the paper-trading `risk_per_trade_pct = 0.5%` in `config/settings.yaml` — different concern, do not merge them.

---

## 9. CLI commands (current, verified against `cli/*.py` and `main.py`)

```
python main.py collect [--exchange binance|kraken|htx|all] [--funding]
python main.py features
python main.py backtest
python main.py validate
python main.py engine [--strategy NAME] [--symbol SYM]
python main.py leaderboard [--tier TIER]
python main.py dashboard [--host HOST] [--port PORT]
python main.py train-classifier / retrain / drift
python main.py research [--strategy NAME] [--symbol SYM] [--dry-run] [--top-n N --tier T]
python main.py paper [--exchange ...] [--replay-days N]
python main.py backfill --symbol SYM --timeframe TF --from DATE --to DATE
python main.py live --exchange binance|kraken|htx
python main.py signal-scan --exchange kraken     # ← the command Agent Orchestration builds on top of
```

No `trader.py` CLI (Section 55 of the spec) exists yet — it is new surface.

---

## 10. Configuration reality check

`config/settings.yaml` + `config/loader.py::Settings` define `account_size` (5000), `max_daily_loss_pct`/`max_drawdown_pct` (the pre-Prop-pivot paper/live floor system, separate from `risk/prop_account.py`'s Kraken-Prop-shaped floors), `risk_per_trade_pct` (0.5%), `max_adverse_move_pct`, `risk_buffer_pct`, and the `ready_for_live_min_dd_pct`/`min_pass_ratio`/`min_paper_trades` trio.

**Not present in `Settings` at all** (confirmed by grep — every reference is a `getattr(cfg, name, DEFAULT)` fallback that always hits the default today): `kraken_prop_mdd_pct`, `pending_signal_risk_pct`, `pending_signal_expiry_minutes`, `pending_signal_default_stop_pct`, `pending_signal_r_multiple`, `pending_signal_expected_hold_hours`, `pending_signal_stale_price_move_pct`, `ready_for_live_min_deflated_sharpe`. These are "config-overridable" in name only — wiring them into `settings.yaml` is a real, small Phase-1-appropriate task if Agent Orchestration wants them tunable without a code change.

---

## 11. Known limitations directly relevant to Agent Orchestration's design

From `README.md`'s Known Gaps/Limitations and confirmed in code:

- Same-bar fills, flat slippage, single-asset mark-to-market are backtest-only simplifications; paper/live trading (CCXT path) already fills against the next available price, not same-bar. Relevant to Section 6's "walk-forward skepticism" — the divergence to watch for is smaller on the live side than the backtest-vs-live framing in the spec implies, since paper/live already isn't same-bar.
- `risk/daily_clock.py` exists but nothing calls it on a schedule (§5 above) — before any "NORMAL/CAUTION/HALTED" state machine (Section 19) can trust a daily rollover boundary, this needs to actually run.
- `strategy_benched` / consecutive-loss tracking are inert fields today — Section 19's emergency-state design assumes live tracking that does not exist yet.
- Zero strategies were DSR-eligible as of the last verified check — Agent Orchestration's "0 eligible strategies is a valid state, report it honestly" instruction (Section 4) matches the codebase's actual current behavior; this is not a hypothetical the spec invented.

---

## 12. Integration points for Agent Orchestration (summary — see `state/integration_plan.md` for the actual plan)

| New component | Reads from (existing, read-only) | Writes to (new) |
|---|---|---|
| Research/strategy-review adapter | `monitoring.leaderboard.build_leaderboard()`, `monitoring.overfitting` fields already on each row | nothing — read-only |
| Account-state observer | `signals.pending_signal_detector.current_account_state()` | nothing — read-only |
| Decision engine | `pending_signals` (status='active'), `gate_decisions` | new `decisions` table |
| Journal | `pending_signals`, `gate_decisions`, `paper_orders` (for cross-reference only) | new `trade_journal`/`execution_journal` tables |
| Escalation | account-state observer, reconciliation | new `incidents` table or log sink |
| Browser execution (later phase) | decision engine's approved intents only | new `execution_intents` table + Kraken terminal via Playwright |

No existing table is proposed to be modified in Phase 1 — everything Agent Orchestration needs is either already read-only-accessible or requires a new, additive table.
