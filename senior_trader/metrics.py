"""Session metrics — AGENT_ORCHESTRATION.md Section 51's "Measure" list,
scoped to what Phase 3's schema can honestly answer today.

Decision quality, stale-signal rate, execution divergence, and risk
adherence (incident counts) are measurable now. missed_opportunities and
false_positives are NOT — both require knowing whether a trade would have
been, or was, profitable, which needs a closed-position outcome. Nothing in
this codebase simulates a position CLOSE yet (Phase 3 only simulates the
entry fill), so those two fields are reported as None with this docstring
as the reason, rather than a fabricated number — matching this codebase's
existing convention (e.g. monitoring/overfitting.py's `underflowed` flag)
of never letting "we don't know" masquerade as zero.
"""
from __future__ import annotations

from senior_trader.models import SessionMetrics


def compute_session_metrics(db) -> SessionMetrics:
    decisions_df = db.read_sql("SELECT decision, no_trade_reason FROM decisions")
    total = len(decisions_df)
    take = int((decisions_df["decision"] == "TAKE").sum()) if total else 0
    no_trade = int((decisions_df["decision"] == "NO_TRADE").sum()) if total else 0
    stale = int((decisions_df["no_trade_reason"] == "NO_TRADE_STALE_SIGNAL").sum()) if total else 0

    intents_df = db.read_sql("SELECT status FROM execution_intents")
    intents_built = len(intents_df)
    filled = int((intents_df["status"] == "filled").sum()) if intents_built else 0
    expired = int((intents_df["status"] == "expired").sum()) if intents_built else 0

    incidents_df = db.read_sql("SELECT category FROM incidents")
    incidents_total = len(incidents_df)
    incidents_by_category = (
        incidents_df["category"].value_counts().to_dict() if incidents_total else {}
    )
    # senior_trader/paper_session.py raises an EXECUTION_ANOMALY incident on
    # a RECONCILIATION_FAILURE rather than persisting reconciliation results
    # to their own table (Phase 3 doesn't have one) — count it from there.
    reconciliation_failures = int(incidents_by_category.get("EXECUTION_ANOMALY", 0))

    return SessionMetrics(
        decisions_total=total, decisions_take=take, decisions_no_trade=no_trade,
        stale_signal_no_trade=stale, intents_built=intents_built, fills_filled=filled,
        fills_expired=expired, reconciliation_failures=reconciliation_failures,
        incidents_total=incidents_total, incidents_by_category=incidents_by_category,
        missed_opportunities=None, false_positives=None,
    )
