"""Read-only adapter onto the `decisions` table (senior_trader/models.py's
DecisionRecord, written by review_session.py).

take_decisions_without_intent() is Phase 3's work queue: every TAKE
decision that hasn't yet had an execution_intent built for it. Composing
Phase 3 this way — reading Phase 1's output rather than being called
inline from review_session.run_review_cycle — keeps that function exactly
as it was: a pure read-only decision recorder, unaware that anything
downstream ever executes.
"""
from __future__ import annotations

from typing import List


def take_decisions_without_intent(db, limit: int = 50) -> List[dict]:
    """TAKE decisions with no matching execution_intents row yet, oldest
    first. A placeholder decision (pending_signal_id IS NULL, written when
    a review cycle found no active signals at all) is never a TAKE, so it
    never appears here."""
    df = db.read_sql(
        """
        SELECT d.decision_id, d.pending_signal_id, d.gate_decision_id, d.evidence, d.created_at
        FROM decisions d
        LEFT JOIN execution_intents ei ON ei.decision_id = d.decision_id
        WHERE d.decision = 'TAKE' AND ei.decision_id IS NULL
        ORDER BY d.created_at ASC
        LIMIT %s
        """,
        params=[limit],
    )
    return df.to_dict("records") if not df.empty else []
