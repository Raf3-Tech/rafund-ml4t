"""Execution intents — AGENT_ORCHESTRATION.md Section 13/14.

build_execution_intent() turns a TAKE decision plus the pending_signals row
it was about into an immutable ExecutionIntent — the only thing any future
execution mechanism (Phase 5+) is allowed to act on. persist_execution_intent
writes it; record_fill_outcome updates its status once
senior_trader/simulation.py (or, later, a real fill) resolves it. Nothing
in this module talks to a browser or an exchange API — see
tests/test_senior_trader_boundaries.py.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional

# Reuses trading.paper_trader's strategy-name slugifier for setup_tag
# generation rather than a second regex — same deliberate-reuse-of-a-
# private-helper pattern as senior_trader.adapters.research's reuse of
# _qualifying_single_symbol_candidates. Pure string function, no side
# effects, no execution capability — trading.paper_trader itself is not on
# the forbidden-import list (only trading.execution/trading.live_trader/
# ccxt/playwright are; see tests/test_senior_trader_boundaries.py).
from trading.paper_trader import _slug

from senior_trader.models import ExecutionIntent


def build_execution_intent(
    decision: dict,
    signal: dict,
    gate: Optional[dict],
    *,
    account: str,
    execution_mode: str,
) -> ExecutionIntent:
    """`decision` is a row from
    senior_trader.adapters.decisions.take_decisions_without_intent;
    `signal` is the matching senior_trader.adapters.signals.signal_by_id
    result; `gate` is senior_trader.adapters.gate.decision_by_id's result
    for decision['gate_decision_id'], or None if that id was itself None
    (should not happen for a real TAKE, but handled honestly rather than
    assumed — AGENT_ORCHESTRATION.md Section 41's "never invent" applies:
    an unresolvable gate decision is reported as "UNKNOWN", not silently
    assumed to be APPROVE)."""
    direction = signal["direction"]
    side = "BUY" if direction == "LONG" else "SELL"
    strategy_id = signal["strategy_name"]
    setup_tag = f"{_slug(strategy_id)}_{direction.lower()}"

    gate_action = gate["action"] if gate is not None else "UNKNOWN"
    gate_snapshot: Dict[str, Any] = (
        {"gate_decision_id": gate["id"], "reason_code": gate["reason_code"]} if gate is not None else {}
    )

    return ExecutionIntent(
        intent_id=str(uuid.uuid4()),
        decision_id=decision["decision_id"],
        pending_signal_id=int(signal["id"]),
        account=account,
        symbol=signal["symbol"],
        side=side,
        order_type="limit",
        price=Decimal(str(signal["limit_price"])),
        quantity=Decimal(str(signal["qty"])) if signal.get("qty") is not None else Decimal("0"),
        reduce_only=False,
        gate_decision=gate_action,
        gate_snapshot=gate_snapshot,
        setup_tag=setup_tag,
        strategy_id=strategy_id,
        expires_at=signal["expires_at"],
        execution_mode=execution_mode,
    )


def persist_execution_intent(db, intent: ExecutionIntent) -> None:
    conn = db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO execution_intents
                (intent_id, decision_id, pending_signal_id, account, symbol, side, order_type,
                 price, quantity, reduce_only, gate_decision, gate_snapshot, setup_tag,
                 strategy_id, expires_at, execution_mode, status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending')
            """,
            (
                intent.intent_id, intent.decision_id, intent.pending_signal_id, intent.account,
                intent.symbol, intent.side, intent.order_type, intent.price, intent.quantity,
                intent.reduce_only, intent.gate_decision, json.dumps(intent.gate_snapshot, default=str),
                intent.setup_tag, intent.strategy_id, intent.expires_at, intent.execution_mode,
            ),
        )
        conn.commit()
        cur.close()
    finally:
        db.return_connection(conn)


def record_fill_outcome(
    db, intent_id: str, *, status: str, fill_price: Optional[Decimal], filled_at: Optional[datetime],
) -> None:
    conn = db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE execution_intents SET status = %s, fill_price = %s, filled_at = %s WHERE intent_id = %s",
            (status, fill_price, filled_at, intent_id),
        )
        conn.commit()
        cur.close()
    finally:
        db.return_connection(conn)
