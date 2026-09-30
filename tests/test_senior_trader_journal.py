"""Tests for senior_trader/journal.py — schema round-trip only (Phase 1 has
no execution capability yet, so nothing populates a real entry in
production; this proves the table/column shape is right)."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

from senior_trader.journal import fetch_journal_entry, write_journal_entry
from senior_trader.models import JournalEntry

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _entry(**overrides) -> JournalEntry:
    base = dict(
        trade_id="11111111-1111-1111-1111-111111111111",
        decision_id="22222222-2222-2222-2222-222222222222",
        pending_signal_id=7,
        setup_tag="wf_breakout_retest",
        symbol="BTC/USD",
        direction="LONG",
        signal_price=Decimal("100"),
        decision_timestamp=NOW,
    )
    base.update(overrides)
    return JournalEntry(**base)


def test_write_journal_entry_writes_expected_columns_and_commits():
    db = MagicMock()
    conn = MagicMock()
    cur = MagicMock()
    db.get_connection.return_value = conn
    conn.cursor.return_value = cur

    entry = _entry()
    write_journal_entry(db, entry)

    cur.execute.assert_called_once()
    sql, params = cur.execute.call_args[0]
    assert "trade_journal" in sql
    assert params[0] == entry.trade_id
    assert params[1] == entry.decision_id
    assert params[3] == entry.setup_tag
    conn.commit.assert_called_once()
    db.return_connection.assert_called_once_with(conn)


def test_fetch_journal_entry_returns_none_when_missing():
    db = MagicMock()
    conn = MagicMock()
    cur = MagicMock()
    db.get_connection.return_value = conn
    conn.cursor.return_value = cur
    cur.fetchone.return_value = None

    result = fetch_journal_entry(db, "missing-id")
    assert result is None
    db.return_connection.assert_called_once_with(conn)


def test_fetch_journal_entry_maps_row_to_dict():
    db = MagicMock()
    conn = MagicMock()
    cur = MagicMock()
    db.get_connection.return_value = conn
    conn.cursor.return_value = cur
    cur.fetchone.return_value = (
        "trade-1", "decision-1", 7, "wf_breakout_retest", "BTC/USD", "LONG", "note",
    )

    result = fetch_journal_entry(db, "trade-1")
    assert result == {
        "trade_id": "trade-1", "decision_id": "decision-1", "pending_signal_id": 7,
        "setup_tag": "wf_breakout_retest", "symbol": "BTC/USD", "direction": "LONG",
        "notes": "note",
    }
