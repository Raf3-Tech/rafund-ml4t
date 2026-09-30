"""Trade journal persistence — the manual-execution journal gap identified
in state/repository_map.md Section 6 (`paper_orders` assumes an automated
fill; `pending_signals` only tracks the proposal; neither can record what a
human actually did with an approved Kraken Prop setup).

Phase 1 only proves the `trade_journal` schema round-trips — nothing yet
calls write_journal_entry from review_session.py, since nothing in this
phase executes a trade. A later phase populates entries once a human
reports back what they actually did.
"""
from __future__ import annotations

from typing import Optional

from senior_trader.models import JournalEntry


def write_journal_entry(db, entry: JournalEntry) -> None:
    conn = db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO trade_journal
                (trade_id, decision_id, pending_signal_id, setup_tag, symbol, direction,
                 signal_price, decision_timestamp, execution_timestamp, fill_price, quantity,
                 close_timestamp, close_price, close_reason, realized_pnl, notes)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (trade_id) DO NOTHING
            """,
            (
                entry.trade_id, entry.decision_id, entry.pending_signal_id, entry.setup_tag,
                entry.symbol, entry.direction, entry.signal_price, entry.decision_timestamp,
                entry.execution_timestamp, entry.fill_price, entry.quantity,
                entry.close_timestamp, entry.close_price, entry.close_reason,
                entry.realized_pnl, entry.notes,
            ),
        )
        conn.commit()
        cur.close()
    finally:
        db.return_connection(conn)


def fetch_journal_entry(db, trade_id: str) -> Optional[dict]:
    conn = db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT trade_id, decision_id, pending_signal_id, setup_tag, symbol, direction, notes
            FROM trade_journal WHERE trade_id = %s
            """,
            (trade_id,),
        )
        row = cur.fetchone()
        cur.close()
    finally:
        db.return_connection(conn)
    if row is None:
        return None
    return {
        "trade_id": row[0], "decision_id": row[1], "pending_signal_id": row[2],
        "setup_tag": row[3], "symbol": row[4], "direction": row[5], "notes": row[6],
    }
