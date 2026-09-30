"""Read-only adapter onto pending_signals — the signal-scan output.

Extracted from senior_trader/review_session.py's original private
_active_pending_signals (Phase 1) so the same read path is available to any
future Senior Trader component, not just the review cycle — and so it can
be exchange-scoped in one place. Phase 1's version queried every exchange's
active signals regardless of the `exchange` argument run_review_cycle was
given; harmless today since signal-scan only ever writes exchange='kraken'
rows, but worth fixing now that this is a shared adapter rather than a
private helper only one caller used.
"""
from __future__ import annotations

from typing import List, Optional

# Shared by active_signals() and signal_by_id() so both return the same
# shape — execution_intent.py's builder depends on that (it reads `qty`,
# `limit_price`, `expires_at` off either result interchangeably).
_FULL_COLUMNS = (
    "id, strategy_name, symbol, exchange, direction, limit_price, "
    "stop_price, take_profit_price, position_size_usd, qty, "
    "leaderboard_score, leaderboard_rank, thesis, source_timeframe, "
    "created_at, expires_at"
)


def active_signals(db, exchange: Optional[str] = None) -> List[dict]:
    """status='active' pending_signals rows — the same population
    monitoring.routes.publish's tablet endpoint shows — restricted to
    `exchange` if given."""
    if exchange is not None:
        df = db.read_sql(
            f"SELECT {_FULL_COLUMNS} FROM pending_signals "
            "WHERE status = 'active' AND exchange = %s ORDER BY created_at DESC",
            params=[exchange],
        )
    else:
        df = db.read_sql(
            f"SELECT {_FULL_COLUMNS} FROM pending_signals WHERE status = 'active' ORDER BY created_at DESC"
        )
    return df.to_dict("records") if not df.empty else []


def signal_by_id(db, pending_signal_id: int) -> Optional[dict]:
    """One pending_signals row by id, regardless of status — so a signal
    that has since expired or been resolved can still be looked up when
    building an execution intent for a TAKE decision made about it earlier
    (senior_trader/paper_session.py's use case)."""
    df = db.read_sql(
        f"SELECT {_FULL_COLUMNS} FROM pending_signals WHERE id = %s",
        params=[pending_signal_id],
    )
    return df.to_dict("records")[0] if not df.empty else None


def recent_signals(
    db, *, exchange: Optional[str] = None, status: Optional[str] = None, limit: int = 20,
) -> List[dict]:
    """Broader audit view — any status (active/expired), for reviewing what
    the detector proposed recently even if it never became, or no longer
    is, active."""
    clauses = []
    params: list = []
    if exchange is not None:
        clauses.append("exchange = %s")
        params.append(exchange)
    if status is not None:
        clauses.append("status = %s")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)

    df = db.read_sql(
        f"""
        SELECT id, strategy_name, symbol, exchange, direction, limit_price, status,
               created_at, expires_at, resolved_at
        FROM pending_signals
        {where}
        ORDER BY created_at DESC
        LIMIT %s
        """,
        params=params,
    )
    return df.to_dict("records") if not df.empty else []
