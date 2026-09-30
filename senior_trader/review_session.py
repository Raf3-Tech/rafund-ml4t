"""Phase 1 read-only review cycle — the first working Senior Trader slice.

Reads today's approved/reduced setups plus current account state, applies
the Decision Framework (AGENT_ORCHESTRATION.md Section 24) using ONLY
information the existing pipeline already computed, and writes one
`decisions` row per active pending_signals row (plus one placeholder row
when there are none at all — Section 23's "even a NO_TRADE gets a decision
record" applies at the session level too, so an empty session is
distinguishable from a session that never ran). Nothing here writes to
`trade_journal` — nothing has been executed.

Does not execute, does not touch a browser, does not call
risk.pretrade_gate.evaluate() a second time: the gate decision already made
for a status='active' row is authoritative and is only *read* here, never
recomputed. See state/integration_plan.md Section 4 for the design this
implements, and Section 6 for what it deliberately leaves out.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import structlog

from senior_trader.adapters.account import current_state as account_current_state
from senior_trader.escalation import RISK_BREACH, check_account_state_consistency, record_incident
from senior_trader.models import (
    NO_TRADE,
    NO_TRADE_NO_ACTIVE_SIGNALS,
    NO_TRADE_STALE_SIGNAL,
    TAKE,
    DecisionRecord,
)

logger = structlog.get_logger(__name__)


def _latest_gate_decision(db, strategy_name: str, symbol: str, direction: str) -> Optional[dict]:
    """Best-effort lookup by natural key (strategy_name, symbol, direction) —
    pending_signals rows carry no gate_decisions foreign key today (they're
    written by two separate statements in
    signals.pending_signal_detector.scan_kraken_signals with no id passed
    between them). Correct as long as no newer REJECT for the same key has
    been persisted since, which holds by construction: a REJECT never
    reaches _upsert_active_signal, so an active row's most recent
    APPROVE/REDUCE gate_decisions entry is the one that actually approved
    it (see state/repository_map.md Section 6 for the linkage gap this
    works around)."""
    conn = db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, action, reason_code, created_at FROM gate_decisions
            WHERE strategy_name = %s AND symbol = %s AND direction = %s
              AND action IN ('APPROVE', 'REDUCE')
            ORDER BY created_at DESC LIMIT 1
            """,
            (strategy_name, symbol, direction),
        )
        row = cur.fetchone()
        cur.close()
    finally:
        db.return_connection(conn)
    if row is None:
        return None
    return {"id": row[0], "action": row[1], "reason_code": row[2], "created_at": row[3]}


def _active_pending_signals(db) -> List[dict]:
    df = db.read_sql(
        """
        SELECT id, strategy_name, symbol, direction, limit_price, expires_at
        FROM pending_signals WHERE status = 'active' ORDER BY created_at DESC
        """
    )
    return df.to_dict("records") if not df.empty else []


def _decide_one(signal: dict, gate: Optional[dict], now: datetime) -> DecisionRecord:
    decision_id = str(uuid.uuid4())
    evidence: Dict[str, Any] = {
        "strategy_name": signal["strategy_name"], "symbol": signal["symbol"],
        "direction": signal["direction"], "limit_price": str(signal["limit_price"]),
    }
    if gate is not None:
        evidence["gate_action"] = gate["action"]
        evidence["gate_reason_code"] = gate["reason_code"]

    expires_at = signal["expires_at"]
    if expires_at is not None and expires_at <= now:
        evidence["expired_at"] = str(expires_at)
        return DecisionRecord(
            decision_id=decision_id, pending_signal_id=int(signal["id"]),
            gate_decision_id=gate["id"] if gate else None,
            decision=NO_TRADE, no_trade_reason=NO_TRADE_STALE_SIGNAL, evidence=evidence,
        )

    return DecisionRecord(
        decision_id=decision_id, pending_signal_id=int(signal["id"]),
        gate_decision_id=gate["id"] if gate else None,
        decision=TAKE, no_trade_reason=None, evidence=evidence,
    )


def _persist_decision(db, record: DecisionRecord) -> None:
    conn = db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO decisions
                (decision_id, pending_signal_id, gate_decision_id, decision, no_trade_reason, evidence)
            VALUES (%s,%s,%s,%s,%s,%s)
            """,
            (
                record.decision_id, record.pending_signal_id, record.gate_decision_id,
                record.decision, record.no_trade_reason, json.dumps(record.evidence, default=str),
            ),
        )
        conn.commit()
        cur.close()
    finally:
        db.return_connection(conn)


@dataclass
class ReviewCycleReport:
    """A SESSION_REPORT-shaped summary (AGENT_ORCHESTRATION.md Section 56).
    Phase 1 only returns/logs it — persisting session reports is a later-
    phase concern once there's more than one field worth trending."""
    exchange: str
    generated_at: datetime
    account_daily_room_remaining: str
    account_lifetime_room_remaining: str
    active_signals_reviewed: int
    decisions_take: int
    decisions_no_trade: int
    incidents_raised: int


def run_review_cycle(db, exchange: str = "kraken") -> ReviewCycleReport:
    now = datetime.now(timezone.utc)

    account = account_current_state(db, exchange=exchange)

    incidents_raised = 0
    issues = check_account_state_consistency(account)
    if issues is not None:
        record_incident(
            db, category=RISK_BREACH, severity="critical", evidence=issues,
            recommended_action="Freeze new execution; verify account state against the Kraken terminal by hand.",
            current_state={
                "daily_room_remaining": str(account.daily_room_remaining),
                "lifetime_room_remaining": str(account.lifetime_room_remaining),
            },
        )
        incidents_raised += 1
        logger.warning("senior_trader_account_state_inconsistent", exchange=exchange, issues=issues)

    signals = _active_pending_signals(db)
    take_count = 0
    no_trade_count = 0

    if not signals:
        placeholder = DecisionRecord(
            decision_id=str(uuid.uuid4()), pending_signal_id=None, gate_decision_id=None,
            decision=NO_TRADE, no_trade_reason=NO_TRADE_NO_ACTIVE_SIGNALS,
            evidence={"exchange": exchange, "active_signal_count": 0},
        )
        _persist_decision(db, placeholder)
        no_trade_count += 1
        logger.info("senior_trader_no_active_signals", exchange=exchange)
    else:
        for signal in signals:
            gate = _latest_gate_decision(db, signal["strategy_name"], signal["symbol"], signal["direction"])
            record = _decide_one(signal, gate, now)
            _persist_decision(db, record)
            if record.decision == TAKE:
                take_count += 1
            else:
                no_trade_count += 1
            logger.info(
                "senior_trader_decision", decision_id=record.decision_id, decision=record.decision,
                no_trade_reason=record.no_trade_reason, symbol=signal["symbol"],
                strategy=signal["strategy_name"],
            )

    return ReviewCycleReport(
        exchange=exchange, generated_at=now,
        account_daily_room_remaining=str(account.daily_room_remaining),
        account_lifetime_room_remaining=str(account.lifetime_room_remaining),
        active_signals_reviewed=len(signals), decisions_take=take_count,
        decisions_no_trade=no_trade_count, incidents_raised=incidents_raised,
    )
