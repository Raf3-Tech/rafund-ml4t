import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from data.verify_data import check_all_symbols, find_gaps


class DummyDB:
    def __init__(self, connection):
        self._connection = connection

    def get_connection(self):
        return self._connection

    def return_connection(self, conn):
        pass


def create_price_table(connection):
    cursor = connection.cursor()
    cursor.execute(
        '''
        CREATE TABLE prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exchange TEXT NOT NULL DEFAULT 'binance',
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL DEFAULT '1d',
            timestamp DATETIME NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL
        )
        '''
    )
    connection.commit()


def add_daily_candle(connection, symbol, dt, timeframe='1d', exchange='binance'):
    cursor = connection.cursor()
    cursor.execute(
        'INSERT INTO prices (exchange, symbol, timeframe, timestamp, open, high, low, close, volume) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (exchange, symbol, timeframe, dt.isoformat(), 1.0, 2.0, 1.0, 2.0, 1.0),
    )
    connection.commit()


def test_find_gaps_returns_missing_daily_timestamp():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    create_price_table(conn)

    add_daily_candle(conn, 'BTC/USDT', datetime(2024, 1, 1, tzinfo=timezone.utc))
    add_daily_candle(conn, 'BTC/USDT', datetime(2024, 1, 2, tzinfo=timezone.utc))
    add_daily_candle(conn, 'BTC/USDT', datetime(2024, 1, 4, tzinfo=timezone.utc))

    db = DummyDB(conn)
    gaps = find_gaps(db, 'BTC/USDT', '1d', date(2024, 1, 1), date(2024, 1, 4))

    assert len(gaps) == 1
    assert gaps[0]['expected_timestamp'].date() == date(2024, 1, 3)
    assert gaps[0]['gap_minutes'] == 1440


def test_find_gaps_returns_empty_when_complete():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    create_price_table(conn)

    for day in range(1, 5):
        add_daily_candle(conn, 'BTC/USDT', datetime(2024, 1, day, tzinfo=timezone.utc))

    db = DummyDB(conn)
    gaps = find_gaps(db, 'BTC/USDT', '1d', date(2024, 1, 1), date(2024, 1, 4))
    assert gaps == []


def test_check_all_symbols_combines_reports():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    create_price_table(conn)

    add_daily_candle(conn, 'BTC/USDT', datetime(2024, 1, 1, tzinfo=timezone.utc))
    add_daily_candle(conn, 'ETH/USDT', datetime(2024, 1, 1, tzinfo=timezone.utc))

    db = DummyDB(conn)
    report = check_all_symbols(db, ['BTC/USDT', 'ETH/USDT'], '1d', date(2024, 1, 1), date(2024, 1, 2))
    assert len(report) == 2
    symbols = {gap['symbol'] for gap in report}
    assert symbols == {'BTC/USDT', 'ETH/USDT'}


def test_find_gaps_ignores_rows_of_other_timeframes():
    conn = sqlite3.connect(':memory:')
    create_price_table(conn)
    add_daily_candle(conn, 'BTC/USDT', datetime(2024, 1, 1, tzinfo=timezone.utc))
    # A 4h candle at 2024-01-02 00:00 must not satisfy the 1d check.
    add_daily_candle(conn, 'BTC/USDT', datetime(2024, 1, 2, tzinfo=timezone.utc), timeframe='4h')

    gaps = find_gaps(DummyDB(conn), 'BTC/USDT', '1d', date(2024, 1, 1), date(2024, 1, 2))
    assert [g['expected_timestamp'].date() for g in gaps] == [date(2024, 1, 2)]


def test_find_gaps_supports_4h_and_exchange_filter():
    conn = sqlite3.connect(':memory:')
    create_price_table(conn)
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    for i in range(6):
        if i != 3:
            add_daily_candle(conn, 'BTC/USDT', start + timedelta(hours=4 * i), timeframe='4h')
    # The missing bar exists only on another exchange.
    add_daily_candle(conn, 'BTC/USDT', start + timedelta(hours=12), timeframe='4h', exchange='kraken')

    gaps = find_gaps(DummyDB(conn), 'BTC/USDT', '4h', date(2024, 1, 1), date(2024, 1, 1), exchange='binance')
    assert len(gaps) == 1
    assert gaps[0]['expected_timestamp'] == start + timedelta(hours=12)
    assert gaps[0]['gap_minutes'] == 240

    assert find_gaps(DummyDB(conn), 'BTC/USDT', '4h', date(2024, 1, 1), date(2024, 1, 1)) == []


def test_find_gaps_rejects_unknown_timeframe():
    with pytest.raises(ValueError):
        find_gaps(DummyDB(None), 'BTC/USDT', '2h', date(2024, 1, 1), date(2024, 1, 1))
