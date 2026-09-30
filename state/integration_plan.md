# Integration Plan — Agent Orchestration (Senior Trader)

Builds on [repository_map.md](repository_map.md). Scope: Phase 1 only (Trader Foundation, per [AGENT_ORCHESTRATION.md](../AGENT_ORCHESTRATION.md) Section 49) — configuration, state/event/decision models, journal schema, escalation model, state machine, logging. **No live trading, no browser code, in this phase.**

---

## 1. Where this lives in the repository

New top-level package `senior_trader/`, sibling to `strategies/`, `risk/`, `trading/` — not nested inside any of them, since it is a consumer of all three, not an extension of one.

```
senior_trader/
├── __init__.py
├── config.py              # SeniorTraderConfig — new, separate from config.loader.Settings
├── models.py               # Decision, DecisionRecord, EscalationEvent dataclasses (Decimal-safe)
├── state_machine.py         # TraderState enum + legal-transition table
├── adapters/
│   ├── __init__.py
│   ├── research.py          # read-only: wraps monitoring.leaderboard.build_leaderboard()
│   └── account.py            # read-only: wraps signals.pending_signal_detector.current_account_state()
├── journal.py               # trade_journal / decision_journal read+write
├── escalation.py             # escalation_manager — writes `incidents`
└── review_session.py         # orchestrates one read-only review cycle (see §4)
```

This mirrors the spec's Section 26 tree but collapses it: Phase 1 needs `agents/`, `orchestration/`, `journal/`, `monitoring/` conceptually, but not as five separate directories yet — that split is worth doing once there's more than one file per concern. Expanding to the full tree is a Phase-3+ refactor, not a Phase-1 requirement.

Rationale for a new top-level package rather than extending `signals/` or `risk/`: `signals/pending_signal_detector.py`'s own docstring is explicit that it "must never import trading.execution, ccxt, or anything that talks to a Kraken order endpoint" — it is deliberately a leaf module. Senior Trader sits **above** it as a consumer, not beside it as a peer; putting Senior Trader code inside `signals/` would blur that boundary the existing code was careful to draw.

---

## 2. Database additions (new tables only — nothing existing is altered)

Three new tables, one new Alembic migration (`0022`):

**`decisions`** — one row per Senior Trader decision, including `NO_TRADE` (spec Section 23):
```
id, decision_id (uuid, unique), pending_signal_id (FK -> pending_signals.id, nullable),
gate_decision_id (FK -> gate_decisions.id, nullable), decision (NO_TRADE | TAKE),
no_trade_reason (nullable, one of the enumerated NO_TRADE_* codes, spec §25),
evidence (JSONB), created_at
```

**`incidents`** — escalation log (spec Section 20):
```
id, event_id (uuid, unique), category (RISK_BREACH | ACCOUNT_STATE_DISAGREEMENT | ...),
severity, current_state (JSONB), observed_state (JSONB), expected_state (JSONB),
evidence (JSONB), recommended_action (text), execution_status, created_at, resolved_at (nullable)
```

**`trade_journal`** — the manual-execution journal gap identified in `repository_map.md` §6:
```
id, trade_id (uuid, unique), decision_id (FK -> decisions.decision_id),
pending_signal_id (FK -> pending_signals.id), setup_tag, symbol, direction,
signal_price, decision_timestamp, execution_timestamp (nullable — filled by a human, later phase),
fill_price (nullable), quantity (nullable), close_timestamp (nullable), close_price (nullable),
close_reason (nullable), realized_pnl (nullable), notes (text), created_at
```

All three follow the existing codebase's pattern exactly: plain psycopg2 + explicit SQL in a dedicated module (matching `risk/pretrade_gate.py::persist_decision` and `trading/position.py::log_order`), Decimal-safe JSON via the existing `_json_safe` helper pattern from `risk/pretrade_gate.py:184`. No ORM is introduced — the rest of the codebase doesn't use one.

---

## 3. Configuration

New `senior_trader/config.py::SeniorTraderConfig`, loaded the same way `config.loader.get_settings()` is (env-var override over `settings.yaml` defaults), **not** added as fields on the existing `Settings` class — Senior Trader's config is a distinct concern (execution mode, escalation thresholds) from the engine/strategy config `Settings` already owns.

First fields needed for Phase 1 (everything else is a later-phase concern):
```yaml
senior_trader:
  execution_mode: RESEARCH   # RESEARCH | PAPER | DRY_RUN — SUPERVISED_BROWSER+ are later phases
  correlation_cap_pct: null   # if set, overrides risk.pretrade_gate's DEFAULT_CORRELATION_CAP_PCT
  consecutive_loss_limit: null  # if set, overrides DEFAULT_CONSECUTIVE_LOSS_LIMIT
```

The `correlation_cap_pct`/`consecutive_loss_limit` override path is worth doing in Phase 1 specifically because `repository_map.md` §3 flagged both as uncalibrated placeholders in `risk/pretrade_gate.py` today — giving a risk owner a real config knob for them, without touching `evaluate()`'s logic, is a small, safe, high-value first change. This does **not** mean Senior Trader computes its own risk numbers (forbidden by spec Section 3) — it means `GateContext` gets its `correlation_cap_pct`/`consecutive_loss_limit` from config instead of always defaulting silently.

---

## 4. Phase 1 read-only review cycle (the actual first working slice)

`senior_trader/review_session.py::run_review_cycle(db, exchange="kraken")` — this is the smallest end-to-end thing worth building and testing before anything else:

```
1. adapters.account.current_state(db, exchange)   # wraps existing current_account_state()
2. adapters.research.eligible_strategies(db)        # wraps build_leaderboard(), filters as
                                                       # _qualifying_single_symbol_candidates already does
3. read pending_signals WHERE status='active'        # today's approved/reduced setups
4. for each active signal: apply the Decision Framework (spec §24) using ONLY
   information already computed (gate decision, DSR fields, account room) —
   no new risk math, no new eligibility math
5. write one `decisions` row per active signal (TAKE or NO_TRADE + reason)
6. if any check in step 4 fails in a way the spec's escalation categories
   cover (e.g. account-state fields look internally inconsistent), write
   an `incidents` row
7. print/return a SESSION_REPORT-shaped summary (spec §56) — no persistence
   of the report itself needed yet, stdout is enough for Phase 1
```

Nothing here executes a trade, touches the browser, or writes to `trade_journal` (that table stays empty until a human reports back what they actually did — a later phase's concern). This cycle is provably safe to run unattended because it only reads existing tables and writes append-only decision/incident rows.

Wire it into the CLI as `python main.py senior-trader-review` (new `cli/senior_trader.py`, following the exact pattern of `cli/signal_scan.py`) rather than the spec's proposed standalone `trader.py` — this repo's convention is one `main.py` entry point routing to `cli/*.py` modules (see `main.py`), and Phase 1 has no reason to diverge from that.

---

## 5. State machine (spec Section 33) — Phase 1 subset

Only the states this phase's read-only cycle actually visits:

```
STARTING → HEALTH_CHECK → OBSERVING → SIGNAL_REVIEW → DECISION_PENDING → NO_TRADE | (TAKE, but not executed yet) → SHUTDOWN
```

`EXECUTION_READY`, `EXECUTING`, `RECONCILING`, `POSITION_MONITORING`, `HALTED`, `ESCALATED` are real states in the full design but have no code path to reach in Phase 1 — implement the enum with all of them (so Phase 2+ doesn't need a migration), but only wire the transitions above. Illegal-transition rejection (spec's `REJECTED → EXECUTING` example) is enforced by a plain lookup table in `state_machine.py`, tested directly — no framework needed for six transitions.

---

## 6. What Phase 1 explicitly does NOT do

Matches the spec's own Section 48 instruction ("do NOT immediately implement autonomous live trading") plus what `repository_map.md` shows isn't safe to build on yet:

- No browser code, no Playwright, no `execution/` package — `trading/execution.py` already exists for the CCXT path and is unrelated; Kraken Prop browser execution is a distinct, later effort gated by Section 11's compliance review.
- No live `kraken_prop_mdd_pct` / `last_rollover` feed — `repository_map.md` §5 shows these are still placeholders; Phase 1's account-state adapter surfaces them as-is (with the same caveats) rather than trying to fix them, since fixing the daily-clock wiring is its own scoped task orthogonal to Agent Orchestration.
- No `consecutive_losses_today` tracking — Phase 1's decision engine reads whatever `GateContext` already reports (currently always 0) rather than inventing a tracking mechanism; building that tracker is a reasonable Phase 2 task once Phase 1's schema (which decisions/incidents happened, in order) exists to compute it from.
- No compliance gate module yet (spec Section 11) — not needed until browser execution is on the table.

---

## 7. Test plan for Phase 1

Follow the existing repo convention (`tests/test_pretrade_gate.py`, `tests/test_pending_signal_detector.py` — plain pytest, DB-backed fixtures, no mocking of the risk math):

- `tests/test_senior_trader_state_machine.py` — every legal transition succeeds, every named illegal one raises.
- `tests/test_senior_trader_decisions.py` — given a fixed `pending_signals`/`gate_decisions` fixture, `run_review_cycle` writes exactly the expected `decisions` rows; a REJECTed gate decision never reaches this layer at all (it never became an active `pending_signals` row, so there's nothing to prove here — the non-bypass guarantee is inherited from `pretrade_gate.py`, not re-tested).
- `tests/test_senior_trader_journal.py` — schema round-trip only in Phase 1 (no execution to journal yet).
- One structural test mirroring `tests/test_publish_routes.py::test_registered_publish_routes_are_get_only`: assert `senior_trader/` contains no import of `ccxt`, `playwright`, or `trading.live_trader` — enforces the "no execution capability in Phase 1" boundary the same way `signals/pending_signal_detector.py` enforces its own no-`trading.execution` boundary today.

---

## 8. Completion criteria (Phase 1 slice of spec Section 64)

```
[x] Repository architecture mapped              — this document + repository_map.md
[ ] Existing risk gate integrated (read-only)    — adapters/research.py, adapters/account.py
[ ] Decision records implemented                 — decisions table + models.py
[ ] Escalation implemented (log-only, no action)  — incidents table + escalation.py
[ ] State machine implemented (Phase 1 subset)    — state_machine.py
[ ] Journal schema implemented (empty until later)— trade_journal table
[ ] CLI entry point                                — cli/senior_trader.py, python main.py senior-trader-review
[ ] Tests per §7 passing
[ ] No existing table altered, no existing module's behavior changed
```

Everything below this line in the full spec (execution intent, compliance gate, browser observer, supervised/autonomous execution, reconciliation, auditor) is out of scope until this slice is reviewed and merged.
