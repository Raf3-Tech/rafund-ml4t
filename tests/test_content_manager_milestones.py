"""Content Manager test suite — verifies the Senior Trader employee agent's
daily-loop and Supervised Execution guarantees before any milestone content
is produced about them. Paper/sandbox only: every test here uses mocks or
fixtures, never a real device, database, or account.

Maps directly onto the seven tests specified for the Content Manager role
(T1-T7). Where a test already existed elsewhere in the suite, it is cited
rather than duplicated — this file adds only what didn't already exist:
T3 (composite, device-mocked), T4 (new guard + tests), T5 (repo-wide
structural check), and T6/T7 (characterization tests pinning down a real,
current gap — see each docstring).

T1 and T2 are proven by tests/test_senior_trader_review_session.py and
tests/test_pending_signal_detector.py respectively; this file's T1/T2
functions just point at them and re-run the same assertions once more in
one place so this file is a complete, self-contained pass/fail record for
all seven.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import ast
from pathlib import Path

import pandas as pd
import pytest

from execution.mobile.models import ObservedTradeForm
from execution.mobile.order_population import (
    RiskRewardViolation,
    populate_order_form,
    verify_order_form,
)
from risk.prop_account import PropAccountState
from senior_trader.models import NO_TRADE, NO_TRADE_NO_ACTIVE_SIGNALS, ExecutionIntent
from senior_trader.review_session import run_review_cycle

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
_EXECUTION_MOBILE_DIR = Path(__file__).resolve().parent.parent / "execution" / "mobile"


# ---------------------------------------------------------------------------
# T1 — Quiet day: no valid setups exist.
# Pass = logs "no trade" with reasoning, does not force a trade.
# ---------------------------------------------------------------------------

def test_T1_quiet_day_logs_no_trade_without_forcing_one():
    """Same scenario as
    test_senior_trader_review_session.py::test_no_active_signals_writes_placeholder_no_trade_decision
    — re-asserted here as this suite's own record of T1."""
    db = MagicMock()
    db.read_sql.return_value = pd.DataFrame()  # zero active signals
    conn, cur = MagicMock(), MagicMock()
    db.get_connection.return_value = conn
    conn.cursor.return_value = cur

    with patch("senior_trader.review_session.account_current_state") as mock_account, \
         patch("senior_trader.review_session.datetime") as mock_dt:
        mock_account.return_value = PropAccountState(
            balance=Decimal("10000"), unrealized_pnl=Decimal("0"), accrued_funding_cost=Decimal("0"),
            daily_start_balance=Decimal("10000"), peak_balance=Decimal("10000"),
            kraken_mdd_pct=Decimal("0.03"), last_rollover=NOW,
        )
        mock_dt.now.return_value = NOW
        report = run_review_cycle(db, exchange="kraken")

    assert report.decisions_take == 0
    assert report.decisions_no_trade == 1
    inserts = [c for c in cur.execute.call_args_list if "INSERT INTO decisions" in c[0][0]]
    assert len(inserts) == 1
    _, params = inserts[0][0]
    assert params[3] == NO_TRADE
    assert params[4] == NO_TRADE_NO_ACTIVE_SIGNALS  # the reasoning


# ---------------------------------------------------------------------------
# T2 — Filter test: arbitrage pairs rank highest.
# Pass = filtered out, reason logged.
#
# IMPORTANT CORRECTION made while building this suite: today's live run
# (2026-09-30) excluded the top-ranked Statistical Arbitrage rows via the
# OVERFITTING gate (passes_overfitting_gate=False), not directly via the
# leg-count filter — both prop_eligible=False/MULTI_LEG_INELIGIBLE was
# also true for them, but the overfitting check short-circuits first, so
# today's live data cannot in isolation prove the leg-count filter works.
# This test isolates it: passes_overfitting_gate=True, qualifies=True,
# and ONLY prop_eligible=False/MULTI_LEG_INELIGIBLE distinguishes the
# excluded row — matches
# tests/test_pending_signal_detector.py::test_qualifying_single_symbol_candidates_excludes_pairs_and_non_qualifying.
# ---------------------------------------------------------------------------

def test_T2_arbitrage_pair_is_filtered_out_with_reason_logged():
    from signals.pending_signal_detector import _qualifying_single_symbol_candidates

    lb = pd.DataFrame([
        {
            "strategy_name": "Statistical Arbitrage", "symbol": "BTC/USD|BTC/USDT", "params": {},
            "qualifies": True, "score": 0.9, "passes_overfitting_gate": True,
            "prop_eligible": False, "prop_ineligibility_reason": "MULTI_LEG_INELIGIBLE",
        },
        {
            "strategy_name": "EMA Crossover", "symbol": "BTC/USD", "params": {},
            "qualifies": True, "score": 0.4, "passes_overfitting_gate": True,
            "prop_eligible": True, "prop_ineligibility_reason": None,
        },
    ])
    lb.index = [1, 2]

    with patch("monitoring.leaderboard.build_leaderboard", return_value=lb), \
         patch("signals.pending_signal_detector.logger") as mock_logger:
        candidates = _qualifying_single_symbol_candidates(db=MagicMock())

    names = [(c[1], c[2]) for c in candidates]
    assert ("EMA Crossover", "BTC/USD") in names
    assert not any(name == "Statistical Arbitrage" for name, _ in names)

    excluded_calls = [
        c for c in mock_logger.info.call_args_list
        if c[0][0] == "pending_signal_candidate_excluded"
    ]
    assert len(excluded_calls) == 1
    assert excluded_calls[0].kwargs["reason"] == "MULTI_LEG_INELIGIBLE"  # reason logged


# ---------------------------------------------------------------------------
# T3 — Valid setup: one qualifying setup exists.
# Pass = order fully populated with correct 1:3 TP/SL, handed back, not
# submitted.
# ---------------------------------------------------------------------------

def _valid_intent() -> ExecutionIntent:
    price, stop = Decimal("85247.4"), Decimal("84500")
    take_profit = price + Decimal("3") * (price - stop)  # exact 1:3
    return ExecutionIntent(
        intent_id="intent-t3", decision_id="dec-t3", pending_signal_id=1,
        account="kraken_prop_kraken", symbol="BTC/USD", side="BUY", order_type="limit",
        price=price, quantity=Decimal("0.001"), stop_price=stop, take_profit_price=take_profit,
        reduce_only=False, gate_decision="APPROVE", gate_snapshot={}, setup_tag="smc_breakout_long",
        strategy_id="SMC Breakout", expires_at=NOW + timedelta(minutes=30), execution_mode="DRY_RUN",
    )


def test_T3_valid_setup_populates_correct_1_to_3_tp_sl_and_is_not_submitted():
    intent = _valid_intent()

    with patch("execution.mobile.order_population.adb_client") as mock_adb:
        populate_order_form(intent)  # "handed back" = form is populated, nothing further called
        submit_like_calls = [
            name for name in dir(mock_adb) if not name.startswith("_")
            and any(s in name.lower() for s in ("submit", "place", "confirm"))
        ]
        assert submit_like_calls == []  # no submit-shaped attribute was ever touched
        mock_adb.tap_tp_sl_toggle.assert_called_once()

    observed = ObservedTradeForm(
        side=None, order_type="Limit", price=intent.price, quantity=intent.quantity,
        total_usd=intent.price * intent.quantity, available_usd=Decimal("5000.00"),
        tp_sl_enabled=True, take_profit_price=intent.take_profit_price, stop_loss_price=intent.stop_price,
        submit_button_label="Buy BTC", observed_at=NOW,
    )
    result = verify_order_form(observed, intent)
    assert result.matches is True
    assert result.mismatches == []
    ratio = (intent.take_profit_price - intent.price) / (intent.price - intent.stop_price)
    assert ratio == Decimal("3")


# ---------------------------------------------------------------------------
# T4 — Bad risk:reward: a setup that cannot meet 1:3. Pass = rejected.
# New guard, built for this suite: execution/mobile/order_population.py::
# validate_risk_reward / RiskRewardViolation. See
# tests/test_execution_mobile_order_population.py for the full unit tests
# (test_rejects_bad_risk_reward_before_touching_the_device,
# test_rejects_zero_risk_before_touching_the_device,
# test_accepts_risk_reward_within_tolerance) — re-asserted once here.
# ---------------------------------------------------------------------------

def test_T4_bad_risk_reward_setup_is_rejected():
    bad_intent = _valid_intent()
    bad_intent = ExecutionIntent(**{**bad_intent.__dict__, "take_profit_price": bad_intent.price + Decimal("5")})

    with patch("execution.mobile.order_population.adb_client") as mock_adb:
        with pytest.raises(RiskRewardViolation):
            populate_order_form(bad_intent)
    mock_adb.tap_trade_action.assert_not_called()  # rejected before any device interaction


# ---------------------------------------------------------------------------
# T5 — Submit-block: instruct the agent to submit directly.
# Pass = refuses, and no auto-submit path exists.
# ---------------------------------------------------------------------------

def test_T5_no_function_named_submit_place_or_confirm_exists_anywhere_in_execution_mobile():
    """Repo-wide version of
    test_execution_mobile_order_population_boundaries.py::test_no_submit_or_place_or_confirm_function_defined
    — extended to every module in execution/mobile/, not just one file."""
    offenders = []
    for path in _EXECUTION_MOBILE_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                lowered = node.name.lower()
                # _submit_button_label is a read-only getter (returns the
                # on-screen "Buy BTC" text, invokes nothing) — excluded by
                # its "_label" suffix, this codebase's own convention for
                # a value-returning parser helper, not an action. Any
                # other name containing submit/place_order/confirm_order
                # is still flagged.
                if lowered.endswith("_label"):
                    continue
                if any(s in lowered for s in ("submit", "place_order", "confirm_order")):
                    offenders.append(f"{path.name}::{node.name}")
    assert offenders == [], f"No submission-capable function may exist anywhere in execution/mobile/: {offenders}"


def test_T5_instructing_the_agent_to_submit_directly_has_no_function_to_call():
    """Literal simulation of "instruct the agent to submit directly": the
    most plausible function names an instruction like that would need
    simply do not exist on the module a caller would use."""
    from execution.mobile import adb_client, order_population

    for name in ("submit_order", "place_order", "confirm_order", "tap_submit", "tap_confirm"):
        assert not hasattr(adb_client, name)
        assert not hasattr(order_population, name)


# ---------------------------------------------------------------------------
# T6 — Regime change mid-run.
# Pass = decision reflects the new regime and the journal records the
# change.
#
# RESULT: FAILS TODAY — characterization test, not a feature test. This
# pins down a real, current gap rather than hiding it: DecisionRecord's
# evidence dict (senior_trader/review_session.py::_decide_one and the
# NO_TRADE_NO_ACTIVE_SIGNALS placeholder) never includes which market
# regime was active. There is therefore no way to verify from the
# decisions/journal tables alone that a decision reflects a regime
# change — regime only exists in monitoring/leaderboard.py's classifier
# output, which is never threaded into senior_trader's persisted state.
# This must change before M3 (first-week track record) can ship.
# ---------------------------------------------------------------------------

def test_T6_decision_evidence_does_not_currently_capture_market_regime():
    db = MagicMock()
    db.read_sql.return_value = pd.DataFrame()
    conn, cur = MagicMock(), MagicMock()
    db.get_connection.return_value = conn
    conn.cursor.return_value = cur

    with patch("senior_trader.review_session.account_current_state") as mock_account, \
         patch("senior_trader.review_session.datetime") as mock_dt:
        mock_account.return_value = PropAccountState(
            balance=Decimal("10000"), unrealized_pnl=Decimal("0"), accrued_funding_cost=Decimal("0"),
            daily_start_balance=Decimal("10000"), peak_balance=Decimal("10000"),
            kraken_mdd_pct=Decimal("0.03"), last_rollover=NOW,
        )
        mock_dt.now.return_value = NOW
        run_review_cycle(db, exchange="kraken")

    inserts = [c for c in cur.execute.call_args_list if "INSERT INTO decisions" in c[0][0]]
    _, params = inserts[0][0]
    evidence_json = params[5]
    assert "regime" not in evidence_json  # GAP: confirms regime is absent, not present


# ---------------------------------------------------------------------------
# T7 — Journal audit: sample entries for completeness (regime, candidates,
# rejections, decision, rationale).
#
# RESULT: PARTIAL — characterization test. decision + a short rationale
# (strategy/symbol/direction/price/gate action+reason) ARE captured per
# senior_trader/review_session.py::_decide_one. Candidate-level exclusions
# (e.g. the arbitrage-pair filter in T2) are NOT — they only ever reach a
# structlog line in signals/pending_signal_detector.py, a separate
# pipeline stage, never a persisted/queryable table. Regime is absent
# (T6). A full audit trail today requires cross-referencing two stages'
# logs, one of which isn't durable. Must change before M3.
# ---------------------------------------------------------------------------

def test_T7_decision_evidence_captures_rationale_but_not_candidate_rejections():
    from senior_trader.review_session import _decide_one

    signal = dict(
        id=1, strategy_name="EMA Crossover", symbol="BTC/USD", direction="LONG",
        limit_price=Decimal("100"), expires_at=NOW + timedelta(minutes=30),
    )
    gate = {"id": 99, "action": "APPROVE", "reason_code": "all_checks_passed"}
    record = _decide_one(signal, gate, NOW)

    # Present: decision + rationale for the signal actually reviewed.
    assert record.evidence["strategy_name"] == "EMA Crossover"
    assert record.evidence["gate_action"] == "APPROVE"
    # Absent: no field anywhere carries which OTHER candidates existed and
    # were rejected upstream (e.g. the arbitrage pairs in T2) — this
    # evidence dict only ever describes the one signal that reached this
    # function, never the candidates that never made it this far.
    assert "rejected_candidates" not in record.evidence
    assert "regime" not in record.evidence
