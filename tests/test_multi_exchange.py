"""Multi-exchange support — exchange column round-trips against a real local DB.

These are integration tests (no DB mocking): trading/position.py and
data/db.py talk to Postgres directly via hardcoded SQL, so a fake cursor
wouldn't exercise the actual ON CONFLICT / column-list behavior we care
about here. All test rows use a "test_" run_id prefix and a dedicated
TEST/XCHG symbol, cleaned up in a finally block.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from cli.db import get_db_connection
from trading.position import PositionState, load_position, log_order, save_position

_SYMBOL = "TEST/XCHG"


@pytest.fixture
def db():
    conn = get_db_connection()
    yield conn
    conn.close_pool()


def _cleanup(db, run_ids: list[str]) -> None:
    conn = db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM paper_orders WHERE run_id = ANY(%s)", (run_ids,))
        cur.execute("DELETE FROM paper_positions WHERE run_id = ANY(%s)", (run_ids,))
        cur.execute("DELETE FROM prices WHERE symbol = %s", (_SYMBOL,))
        conn.commit()
        cur.close()
    finally:
        db.return_connection(conn)


def test_position_round_trip_preserves_exchange(db):
    run_id = "test_paper_kraken_roundtrip"
    try:
        pos = load_position(db, run_id, initial_capital=1000.0, strategy_name="Test",
                             symbol=_SYMBOL, exchange="kraken")
        assert pos.exchange == "kraken"
        pos.side, pos.qty, pos.entry_price = "LONG", 1.5, 100.0
        save_position(db, pos)

        reloaded = load_position(db, run_id, exchange="kraken")
        assert reloaded.exchange == "kraken"
        assert reloaded.side == "LONG"
        assert reloaded.qty == 1.5
    finally:
        _cleanup(db, [run_id])


def test_manual_halt_round_trip(db):
    run_id = "test_paper_kraken_halt"
    try:
        pos = load_position(db, run_id, initial_capital=1000.0, strategy_name="Test",
                             symbol=_SYMBOL, exchange="kraken")
        assert pos.manual_halt is False
        pos.manual_halt = True
        save_position(db, pos)

        reloaded = load_position(db, run_id, exchange="kraken")
        assert reloaded.manual_halt is True
    finally:
        _cleanup(db, [run_id])


def test_two_exchanges_with_same_strategy_dont_collide(db):
    run_binance = "test_paper_binance_collide"
    run_kraken = "test_paper_kraken_collide"
    try:
        pos_b = load_position(db, run_binance, initial_capital=1000.0,
                               strategy_name="Test", symbol=_SYMBOL, exchange="binance")
        pos_b.side = "LONG"
        save_position(db, pos_b)

        pos_k = load_position(db, run_kraken, initial_capital=2000.0,
                               strategy_name="Test", symbol=_SYMBOL, exchange="kraken")
        pos_k.side = "SHORT"
        save_position(db, pos_k)

        reloaded_b = load_position(db, run_binance, exchange="binance")
        reloaded_k = load_position(db, run_kraken, exchange="kraken")
        assert reloaded_b.side == "LONG" and reloaded_b.exchange == "binance"
        assert reloaded_k.side == "SHORT" and reloaded_k.exchange == "kraken"
    finally:
        _cleanup(db, [run_binance, run_kraken])


def test_log_order_records_exchange(db):
    run_id = "test_paper_kraken_order"
    try:
        pos = PositionState(run_id=run_id, strategy_name="Test", symbol=_SYMBOL,
                             exchange="kraken", side="LONG", qty=1.0)
        log_order(db, pos, "OPEN", 50.0)

        df = db.read_sql("SELECT exchange FROM paper_orders WHERE run_id = %s", [run_id])
        assert not df.empty
        assert df.iloc[0]["exchange"] == "kraken"
    finally:
        _cleanup(db, [run_id])


def test_insert_prices_same_symbol_two_exchanges_no_collision(db):
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    df = pd.DataFrame([{
        "symbol": _SYMBOL, "timestamp": ts, "open": 1.0, "high": 1.0,
        "low": 1.0, "close": 1.0, "volume": 1.0,
    }])
    try:
        n_binance = db.insert_prices(df, exchange="binance")
        n_kraken = db.insert_prices(df, exchange="kraken")
        assert n_binance == 1
        assert n_kraken == 1  # same symbol+timestamp, different exchange — not a conflict

        rows = db.read_sql(
            "SELECT exchange FROM prices WHERE symbol = %s ORDER BY exchange", [_SYMBOL]
        )
        assert list(rows["exchange"]) == ["binance", "kraken"]
    finally:
        _cleanup(db, [])


def test_get_latest_timestamp_filters_by_exchange(db):
    ts = datetime(2026, 1, 2, tzinfo=timezone.utc)
    df = pd.DataFrame([{
        "symbol": _SYMBOL, "timestamp": ts, "open": 1.0, "high": 1.0,
        "low": 1.0, "close": 1.0, "volume": 1.0,
    }])
    try:
        db.insert_prices(df, exchange="kraken")
        assert db.get_latest_timestamp(_SYMBOL, exchange="kraken") is not None
        assert db.get_latest_timestamp(_SYMBOL, exchange="binance") is None
    finally:
        _cleanup(db, [])
