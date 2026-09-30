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
[x] Existing risk gate integrated (read-only)    — adapters/research.py, adapters/account.py
[x] Decision records implemented                 — decisions table + models.py
[x] Escalation implemented (log-only, no action)  — incidents table + escalation.py
[x] State machine implemented (Phase 1 subset)    — state_machine.py
[x] Journal schema implemented (empty until later)— trade_journal table
[x] CLI entry point                                — cli/senior_trader.py, python main.py senior-trader-review
[x] Tests per §7 passing                           — 24 new tests, full suite 574 passed
[x] No existing table altered, no existing module's behavior changed
```

**Status: Phase 1 done** (commit `75975c4`, branch `feature/agent-orchestration`). One deliberate deviation from §7: no separate structural test file was added for the `senior_trader/` boundary check — it landed as `tests/test_senior_trader_boundaries.py` rather than being folded into another file, same effect. The `correlation_cap_pct`/`consecutive_loss_limit` config values (§3) are implemented and tested but intentionally **not** wired into `signals/pending_signal_detector.py::scan_kraken_signals` — see `senior_trader/config.py`'s docstring for why that stays a follow-up rather than a Phase 1 change.

Everything below this line in the full spec (execution intent, compliance gate, browser observer, supervised/autonomous execution, reconciliation, auditor) is out of scope until this slice is reviewed and merged.

---

## 9. Phase 2 — Research Adapter (AGENT_ORCHESTRATION.md Section 50)

**Status: done** (branch `feature/agent-orchestration`). Section 50 asks for read-only adapters covering: signal-scan, strategy eligibility, pretrade gate, backtest results, walk-forward results, DSR metrics, account state, positions, orders. Account state and strategy eligibility already existed from Phase 1 (`adapters/account.py`, `adapters/research.py::eligible_candidates`); Phase 2 filled in the rest and hardened what was there:

| Spec category | Module | Notes |
|---|---|---|
| signal-scan | `adapters/signals.py` | `active_signals()`, `recent_signals()`. Extracted from `review_session.py`'s Phase 1 private helper — and fixed to actually scope by `exchange`, which the private version silently didn't. |
| strategy eligibility | `adapters/research.py` | `eligible_candidates()` (Phase 1) + new `leaderboard_row()`, a full-row lookup other adapters build on. |
| pretrade gate | `adapters/gate.py` | `latest_decision()` (extracted from Phase 1's private helper) + new `recent_decisions()` for auditing rejects, not just what approved an active signal. |
| backtest results / walk-forward results | `adapters/backtest.py` | One adapter, not two — `engine_results` is this codebase's only result store for both (repository_map.md's architecture section); `walk_forward_windows()` returns the un-aggregated per-window rows `build_leaderboard()` collapses away. |
| DSR metrics | `adapters/dsr.py` | `dsr_metrics()` — a thin projection of `research.leaderboard_row()`, never a second deflated-Sharpe computation. |
| account state | `adapters/account.py` | Unchanged behavior; refactored to build on the new `adapters/positions.py` instead of loading its own `PositionState` copy. |
| positions | `adapters/positions.py` | New. `current_position()` — Kraken Prop's single `run_id="live_{exchange}"` slot, not the multi-slot paper-trading model. |
| orders | `adapters/orders.py` | New. `recent_fills()` against `paper_orders` — context only; there is still nowhere a manually-executed Kraken Prop fill gets journaled (that's `senior_trader/journal.py`'s `trade_journal`, still empty until a later phase). |

`review_session.py` was refactored to call `adapters.signals.active_signals()` / `adapters.gate.latest_decision()` instead of its own private copies — same behavior, one exchange-scoping bug fixed, zero new risk logic. 26 new tests (one file per adapter module, matching this repo's existing convention), full suite 600 passed.

---

## 10. Phase 3 — Paper Trader (AGENT_ORCHESTRATION.md Section 51)

**Status: done.** Section 51 asks for the full workflow — decision, execution intent, simulated execution, journal, reconciliation, audit — with no real execution, plus measuring decision quality, missed opportunities, false positives, stale signals, execution divergence, and risk adherence.

**New modules:**

| Piece | Module | Notes |
|---|---|---|
| Execution intent | `execution_intent.py` | `build_execution_intent()` — never invents a gate decision: reports `"UNKNOWN"` rather than assuming APPROVE if the natural-key lookup ever came up empty. |
| Simulated execution | `simulation.py` | Deliberately simple: fills at the intent's own limit price, or expires — no fill-probability model. Documented as a simplification, not a claim about real fill rates. |
| Reconciliation | `reconciliation.py` | Compares intent vs. fill; unreachable-failure today (the simulator can't produce a mismatch by construction) but it's the tested seam a later phase's real, imperfect browser-observed fill plugs into unchanged. |
| Auditor | `auditor.py` | Four checks against Phase 1-3's actual schema (every TAKE backed by a real APPROVE/REDUCE, no orphan intents, no generic setup tags, only factual close reasons). Section 39's weekly/strategy-review report types are out of scope — not enough history yet to mean anything. |
| Metrics | `metrics.py` | Decision volume, stale-signal rate, fill rate, incident counts are real. `missed_opportunities`/`false_positives` are `None` on purpose — both need a closed-position outcome, and nothing simulates a CLOSE yet. |
| Orchestrator | `paper_session.py` | `run_paper_trading_cycle()` — reads TAKE decisions with no execution intent yet (`adapters/decisions.py`, new), builds + simulates + reconciles + journals each, runs the daily audit. Composed on top of Phase 1/2, not folded into `review_session.py`. |

**Gated behind config, not a flag no one will remember:** `senior_trader.execution_mode` (Phase 1's config, unused until now) must be `PAPER` for this to do anything — the default `RESEARCH` makes `run_paper_trading_cycle` a provable no-op. This gives Section 42's mode progression (RESEARCH → PAPER → DRY_RUN → …) real teeth instead of being a documented-but-unenforced convention.

**State machine extended, and actually driven at runtime:** `EXECUTING`/`RECONCILING`/`ESCALATED` were added to `state_machine.py`'s legal-transition table, and `paper_session.py` calls `transition()` through the real `EXECUTION_READY → EXECUTING → RECONCILING → (SIGNAL_REVIEW | ESCALATED)` sequence for every intent it processes — not just a table that exists for its own sake. `ESCALATED` has no path back to a trading state, matching Section 19's "never trade out of a breach."

New CLI: `python main.py senior-trader-paper --exchange kraken`. 36 new tests (execution intent, simulation, reconciliation, auditor, metrics, the new `decisions` adapter, the orchestrator's full wiring, and 3 new state-machine transition tests replacing the one Phase 1 test whose assumption Phase 3 deliberately supersedes). Full suite: 636 passed. The boundary test (no `ccxt`/`playwright`/`trading.execution`/`trading.live_trader` import anywhere in `senior_trader/`) still passes unchanged — nothing in this phase can place a real order.

---

## 11. Phase 4 — Browser Observer (AGENT_ORCHESTRATION.md Section 52)

**Status: done, but load-bearing caveat below.** This is the first phase touching anything outside this repository's own database — a real (eventually) Kraken browser session. It is architecturally quarantined from everything built in Phases 1-3:

- New top-level package `execution/` (sibling to `senior_trader/`, matching the spec's own Section 26 tree) — not nested inside `senior_trader/`, so `tests/test_senior_trader_boundaries.py` continues to guarantee, unchanged, that `senior_trader/` imports nothing execution-capable. Verified: zero references to `execution/` anywhere under `senior_trader/`.
- `execution/compliance_gate.py` implements Section 11 exactly: `LIVE_BROWSER_EXECUTION_AUTHORIZED = False` as a literal constant, plus two independent env-var-driven checks (`KRAKEN_BROWSER_OBSERVATION_AUTHORIZED`, `KRAKEN_LIVE_BROWSER_EXECUTION_AUTHORIZED`) — never derived from editing source, so a `git log` never shows a commit "turning on" live trading.
- `execution/browser/browser_manager.py::kraken_terminal_session()` is the only function allowed to launch a browser, and it calls the compliance gate before doing anything else — before even importing `playwright`.
- `execution/browser/kraken_terminal.py::KrakenTerminalObserver` covers every item in Section 52's checklist (account selector/Prop account/balance/equity/risk limits → `observe_account_state`; positions → `observe_positions`; orders/order status → `observe_open_orders`; market/order book → `observe_market`, top-of-book only — this system never chases the book, so depth was never needed; order form → `observe_order_form`; confirmation → `observe_confirmation_state`). It structurally cannot submit an order: there is no submit/place/cancel/modify method on the class at all, enforced by a test that scans its actual method names, not a docstring promise.

**The caveat, stated as plainly as the rest of this document tries to:** `execution/browser/locators.py`'s role/name selectors are **unverified placeholders**. This session has never had access to Kraken's live terminal DOM or accessibility tree — every locator is a best guess at common trading-UI patterns. The parsing/mapping logic in `kraken_terminal.py` (money parsing, table-row extraction, form-field reading) is real and tested against fake Page/Locator doubles that mimic Playwright's call shape; the actual selectors are not tested against anything real and will not work against the live Kraken terminal until a human corrects them using Playwright's inspector or an accessibility snapshot. Treat this phase as "the architecture and safety boundary are done; the DOM binding is a follow-up task for whoever has real terminal access."

**playwright is not installed anywhere in this environment**, and this session never attempted to install it or connect to a real Kraken account — every module in `execution/browser/` uses function-local (or `TYPE_CHECKING`-guarded) `playwright` imports specifically so the package stays importable, and its logic testable, without the dependency present. `requirements.txt` gained `playwright>=1.40` as an explicitly optional, documented line — installing it is a decision for whoever runs this for real, not something this session did.

40+ new tests: compliance gate (env-var matrix), two structural boundary tests (no submission-capable method; `playwright` never imported at module top level; no `ccxt` import), `browser_manager`'s gate-enforcement path, and `kraken_terminal`'s full parsing surface. Full suite: 666 passed.

**Before this phase means anything against a real account:** per Section 11, a human must review Kraken's Terms of Service, Prop Evaluation Terms, and Funded Trader Agreement for automation restrictions, then set `KRAKEN_BROWSER_OBSERVATION_AUTHORIZED=1` and correct `execution/browser/locators.py` against the real terminal. Nothing in this codebase does either of those things automatically, and nothing should.

### 11a. Phase 4 continuation — Mobile (ADB) observer, real selectors

The browser path above was speculative because nobody had real Kraken terminal access. This project already had something better: a real accessibility-tree capture of the actual Kraken Pro **Android app** (`com.kraken.trade` v5.70.1), taken via ADB/uiautomator from a physical Samsung Galaxy Tab A9+ in an earlier session — `docs/mobile_nav/kraken_app/NAV_TREE.md` (gitignored, account-specific, not part of this repo). README's own Architecture Notes already referenced this as the actual basis for the manual-execution workflow. The web browser path (Section 11 above) was this project's first guess at Section 52's Browser Observer; ADB against the real app turns out to be the accurate target.

New package `execution/mobile/`, same quarantine pattern as `execution/browser/` (sibling to `senior_trader/`, own compliance flag `KRAKEN_MOBILE_OBSERVATION_AUTHORIZED`, independent of the browser flag):

- `adb_client.py` — subprocess wrapper exposing only `devices`, `dumpsys` (window focus, call state), `uiautomator dump`, `screencap`. No `input tap`/`input text`/`input swipe`/`am`/`pm` anywhere — enforced by a structural test that greps the actual source, not a comment.
- `parser.py` — parses the raw XML dump into `ObservedPropAccountScreen` (account label, total/target equity, daily loss limit, drawdown limit, favorites list, positions/orders panel state). **Verified against the real captured file** (not committed) — every field matched exactly: `Prop • Advanced Eval 1`, `$5,000.00` equity, `$4,850.00` daily loss and drawdown limits, all 8 favorites. This is a materially stronger result than the browser path's placeholder locators — the parsing logic is proven against real data, not guessed.
- `kraken_app_observer.py` — `observe_prop_account_screen()`, which checks window focus and call state before trusting a dump (both real footguns NAV_TREE.md's own capture notes documented: a stale/wrong-app dump, and a live call overlay observed mid-capture).
- A synthetic, committed fixture (`tests/fixtures/kraken_app_prop_screen_fixture.xml`, fabricated numbers, structurally identical to the real capture) backs the committed test suite — the real captured file is never copied into git, only used locally as one-time ground truth during development.

One known, documented gap: only the *empty* positions/orders state has ever been observed (the captured account had no open positions). A populated table's row structure is unknown and unparsed — `ObservedPositionsPanel.positions`/`.orders` will stay empty even against a real populated screen until someone captures that state and extends the parser.

26 new tests. Full suite (including Phase 4's browser-path tests): 692 collected, 691 passed — the one failure is `tests/test_live_signal_risk.py`'s `PropFirmRiskGuard` drawdown test, caused by an uncommitted, pre-existing local modification to `portfolio/risk.py` that this session did not make and has not touched (verified via `git diff`: the committed version's equity-floor drawdown logic differs from what's currently on disk). Unrelated to Agent Orchestration; left alone.

Phase 5 (Supervised Execution — a human-in-the-loop order preview/confirm flow) is the next spec phase. It can now build on a *verified* observation path (the mobile one) rather than waiting on browser locator verification — though the same "no submission capability" structural discipline must carry forward into whatever executes on the confirmed side.
