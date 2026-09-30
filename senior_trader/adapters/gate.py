"""Read-only adapter onto gate_decisions — the pretrade gate's audit log.

Extracted from senior_trader/review_session.py's original private
_latest_gate_decision (Phase 1) so the same read path is available to any
future Senior Trader component. Never re-evaluates risk.pretrade_gate.
evaluate() — only reads what it already decided.
"""
from __future__ import annotations

from typing import List, Optional


def latest_decision(db, strategy_name: str, symbol: str, direction: str) -> Optional[dict]:
    """Best-effort lookup by natural key (strategy_name, symbol, direction)
    — see state/repository_map.md Section 6 for why pending_signals carries
    no gate_decisions foreign key today, and senior_trader/review_session.py
    for why this is still correct for a currently-active signal despite
    that gap (a REJECT never reaches 'active' status, so an active row's
    most recent APPROVE/REDUCE gate_decisions entry is the one that
    actually approved it)."""
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


def recent_decisions(
    db, *, strategy_name: Optional[str] = None, symbol: Optional[str] = None, limit: int = 20,
) -> List[dict]:
    """Broader audit view across every action (APPROVE/REDUCE/REJECT), not
    just the one that approved a currently-active signal — useful for
    reviewing why a candidate keeps getting rejected."""
    clauses = []
    params: list = []
    if strategy_name is not None:
        clauses.append("strategy_name = %s")
        params.append(strategy_name)
    if symbol is not None:
        clauses.append("symbol = %s")
        params.append(symbol)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)

    df = db.read_sql(
        f"""
        SELECT id, strategy_name, symbol, direction, action, reason_code, created_at
        FROM gate_decisions
        {where}
        ORDER BY created_at DESC
        LIMIT %s
        """,
        params=params,
    )
    return df.to_dict("records") if not df.empty else []
