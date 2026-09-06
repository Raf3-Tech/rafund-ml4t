"""Tests for data/provenance.py — Kraken-sourced instrument provenance."""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from data.provenance import (
    audit_instrument_provenance,
    backfill_engine_results_exchange,
    load_provenance_map,
    sync_instrument_provenance,
)


class MockPricesDB:
    """Mocks db.read_sql for the GROUP BY symbol, exchange audit query, and
    records what sync_instrument_provenance would write via get_connection."""

    def __init__(self, price_rows, provenance_rows=None):
        self._price_rows = pd.DataFrame(price_rows)
        self._provenance_rows = pd.DataFrame(provenance_rows or [])
        self.executed = []

    def read_sql(self, query, params=None):
        if "instrument_provenance" in query:
            return self._provenance_rows
        return self._price_rows

    def get_connection(self):
        return MagicMock()

    def return_connection(self, conn):
        pass


def _install_cursor_recorder(db):
    conn = db.get_connection()
    cur = MagicMock()
    conn.cursor.return_value = cur
    db.get_connection = lambda: conn
    return cur


def test_audit_single_sourced_symbol_is_kraken_when_exchange_is_kraken():
    rows = [{"symbol": "BTC/USD", "exchange": "kraken", "n_rows": 799}]
    result = audit_instrument_provenance(MockPricesDB(rows))
    row = result.iloc[0]
    assert row["source_exchange"] == "kraken"
    assert row["is_kraken_sourced"] == True  # noqa: E712 -- numpy.bool_, not Python bool


def test_audit_single_sourced_non_kraken_is_not_kraken_sourced():
    rows = [{"symbol": "BNB/USDT", "exchange": "binance", "n_rows": 6349}]
    result = audit_instrument_provenance(MockPricesDB(rows))
    row = result.iloc[0]
    assert row["source_exchange"] == "binance"
    assert row["is_kraken_sourced"] == False  # noqa: E712 -- numpy.bool_, not Python bool


def test_audit_mixed_source_symbol_has_no_single_source_exchange():
    """Provenance can't be established as a single source — ineligible,
    same as any other non-Kraken instrument, not a special "maybe" state."""
    rows = [
        {"symbol": "BTC/USDT", "exchange": "binance", "n_rows": 6532},
        {"symbol": "BTC/USDT", "exchange": "htx", "n_rows": 3238},
    ]
    result = audit_instrument_provenance(MockPricesDB(rows))
    row = result.iloc[0]
    assert row["source_exchange"] is None
    assert row["is_kraken_sourced"] == False  # noqa: E712 -- numpy.bool_, not Python bool
    assert set(row["exchanges"]) == {"binance", "htx"}


def test_audit_empty_prices_returns_empty_frame():
    result = audit_instrument_provenance(MockPricesDB([]))
    assert result.empty


def test_sync_upserts_without_ever_writing_prop_verified():
    rows = [{"symbol": "BTC/USD", "exchange": "kraken", "n_rows": 799}]
    db = MockPricesDB(rows)
    cur = _install_cursor_recorder(db)

    n = sync_instrument_provenance(db)

    assert n == 1
    cur.execute.assert_called_once()
    sql, params = cur.execute.call_args[0]
    assert "prop_verified" not in sql.split("SET")[1]  # not in the UPDATE clause
    assert params == ("BTC/USD", "kraken", True)


def test_load_provenance_map_shape():
    provenance_rows = [
        {"symbol": "BTC/USD", "is_kraken_sourced": True, "prop_verified": False},
        {"symbol": "BNB/USDT", "is_kraken_sourced": False, "prop_verified": False},
    ]
    db = MockPricesDB([], provenance_rows)
    result = load_provenance_map(db)
    assert result["BTC/USD"] == {"is_kraken_sourced": True, "prop_verified": False}
    assert result["BNB/USDT"] == {"is_kraken_sourced": False, "prop_verified": False}


def test_load_provenance_map_missing_symbol_is_absent_not_defaulted_true():
    """A symbol never audited/synced must not silently appear eligible —
    callers must treat absence as ineligible."""
    db = MockPricesDB([], [{"symbol": "BTC/USD", "is_kraken_sourced": True, "prop_verified": False}])
    result = load_provenance_map(db)
    assert "SOME/UNKNOWN" not in result


# ── backfill_engine_results_exchange ────────────────────────────────────────

class _FakeCursor:
    def __init__(self, pair_symbols=None):
        self.executed = []
        self._pair_symbols = pair_symbols or []
        self._fetch_result = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "SELECT DISTINCT symbol" in sql:
            self._fetch_result = [(s,) for s in self._pair_symbols]
        elif sql.strip().startswith("UPDATE"):
            self.rowcount = 1

    def fetchall(self):
        return self._fetch_result

    def close(self):
        pass


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def commit(self):
        pass


class BackfillDB(MockPricesDB):
    def __init__(self, price_rows, pair_symbols=None):
        super().__init__(price_rows)
        self.cursor = _FakeCursor(pair_symbols)
        self.conn = _FakeConn(self.cursor)

    def get_connection(self):
        return self.conn

    def return_connection(self, conn):
        pass


def _update_calls(db, symbol=None):
    calls = [c for c in db.cursor.executed if c[0].strip().startswith("UPDATE")]
    if symbol is not None:
        calls = [c for c in calls if c[1][1] == symbol]
    return calls


def test_backfill_sets_inferred_from_symbol_for_single_sourced_symbols():
    rows = [{"symbol": "BTC/USD", "exchange": "kraken", "n_rows": 799}]
    db = BackfillDB(rows)
    counts = backfill_engine_results_exchange(db)
    assert counts["single_leg_backfilled"] == 1
    calls = _update_calls(db, "BTC/USD")
    assert len(calls) == 1
    assert calls[0][1] == ("kraken", "BTC/USD")


def test_backfill_never_touches_ambiguous_symbols():
    rows = [
        {"symbol": "BTC/USDT", "exchange": "binance", "n_rows": 1},
        {"symbol": "BTC/USDT", "exchange": "htx", "n_rows": 1},
    ]
    db = BackfillDB(rows)
    counts = backfill_engine_results_exchange(db)
    assert counts["single_leg_backfilled"] == 0
    assert _update_calls(db, "BTC/USDT") == []


def test_backfill_pairs_combines_both_legs_when_exchanges_differ():
    rows = [
        {"symbol": "BTC/USD", "exchange": "kraken", "n_rows": 799},
        {"symbol": "ETH/USDT", "exchange": "binance", "n_rows": 500},
    ]
    db = BackfillDB(rows, pair_symbols=["BTC/USD|ETH/USDT"])
    counts = backfill_engine_results_exchange(db)
    assert counts["pairs_backfilled"] == 1
    calls = _update_calls(db, "BTC/USD|ETH/USDT")
    assert calls[0][1][0] == "kraken+binance"


def test_backfill_pairs_skipped_when_either_leg_ambiguous():
    rows = [
        {"symbol": "BTC/USD", "exchange": "kraken", "n_rows": 799},
        {"symbol": "ETH/USDT", "exchange": "binance", "n_rows": 1},
        {"symbol": "ETH/USDT", "exchange": "htx", "n_rows": 1},
    ]
    db = BackfillDB(rows, pair_symbols=["BTC/USD|ETH/USDT"])
    counts = backfill_engine_results_exchange(db)
    assert counts["pairs_backfilled"] == 0
    assert _update_calls(db, "BTC/USD|ETH/USDT") == []
