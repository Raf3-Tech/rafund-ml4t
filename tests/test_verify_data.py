import sqlite3
from datetime import date, datetime, timezone

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
            symbol TEXT NOT NULL,
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


def add_daily_candle(connection, symbol, dt):
    cursor = connection.cursor()
    cursor.execute(
        'INSERT INTO prices (symbol, timestamp, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (symbol, dt.isoformat(), 1.0, 2.0, 1.0, 2.0, 1.0),
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
