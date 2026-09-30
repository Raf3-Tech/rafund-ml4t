"""Escalation log — append-only `incidents` table writer
(AGENT_ORCHESTRATION.md Section 20).

Phase 1 defines the categories and the writer, plus exactly one detection
check (check_account_state_consistency below). Nothing in this phase
auto-resolves an incident, and nothing attempts to "trade out" of one
(Section 19). Detection logic beyond that one check is a later-phase
concern — Phase 1 deliberately does not invent new risk math to detect
breaches the pretrade gate doesn't already compute.
"""
from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any, Dict, Optional

from risk.prop_account import PropAccountState

RISK_BREACH = "RISK_BREACH"
ACCOUNT_STATE_DISAGREEMENT = "ACCOUNT_STATE_DISAGREEMENT"
NEW_STRATEGY_ELIGIBILITY = "NEW_STRATEGY_ELIGIBILITY"
EXECUTION_ANOMALY = "EXECUTION_ANOMALY"
DATA_ANOMALY = "DATA_ANOMALY"
BACKTEST_LIVE_DIVERGENCE = "BACKTEST_LIVE_DIVERGENCE"
PLATFORM_CHANGE = "PLATFORM_CHANGE"
COMPLIANCE_BLOCK = "COMPLIANCE_BLOCK"
SYSTEM_FAILURE = "SYSTEM_FAILURE"


def _json_safe(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def record_incident(
    db,
    *,
    category: str,
    severity: str,
    evidence: Dict[str, Any],
    recommended_action: str,
    current_state: Optional[Dict[str, Any]] = None,
    observed_state: Optional[Dict[str, Any]] = None,
    expected_state: Optional[Dict[str, Any]] = None,
) -> str:
    """Append one row to `incidents`. Returns the generated event_id."""
    event_id = str(uuid.uuid4())
    conn = db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO incidents
                (event_id, category, severity, current_state, observed_state, expected_state,
                 evidence, recommended_action, execution_status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'none')
            """,
            (
                event_id, category, severity,
                json.dumps(_json_safe(current_state or {})),
                json.dumps(_json_safe(observed_state or {})),
                json.dumps(_json_safe(expected_state or {})),
                json.dumps(_json_safe(evidence)),
                recommended_action,
            ),
        )
        conn.commit()
        cur.close()
    finally:
        db.return_connection(conn)
    return event_id


def check_account_state_consistency(account: PropAccountState) -> Optional[Dict[str, Any]]:
    """Kraken's own MDL/MDD floors should never actually be breached — our
    internal hard floors (2% daily / 70%-of-tier lifetime) are deliberately
    tighter than Kraken's real limits (risk/prop_account.py's module
    docstring: "our floors sit inside Kraken's"). If Kraken's own floor has
    gone negative anyway, our safety net failed to stop trading in time —
    that is a genuine RISK_BREACH worth a human's attention regardless of
    what caused it, not a normal operating state.

    Returns an evidence dict if something looks internally inconsistent,
    else None. Does not itself decide severity or write anything."""
    issues: Dict[str, Any] = {}
    if account.daily_room_remaining < 0:
        issues["kraken_mdl_breached"] = str(account.daily_room_remaining)
    if account.lifetime_room_remaining < 0:
        issues["kraken_mdd_breached"] = str(account.lifetime_room_remaining)
    return issues or None
