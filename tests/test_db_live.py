"""Live-Postgres tests for the read path and the data-integrity check.

These need a real, migrated database (``alembic upgrade head``) and only run
when ``RAFUND_LIVE_DB=1`` is set — CI's ``db`` job sets it. Without it they are
reported as skipped (with the reason), never silently passed.
"""

import os
from datetime import date, datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RAFUND_LIVE_DB") != "1",
    reason="live Postgres tests: set RAFUND_LIVE_DB=1 with a migrated DATABASE_URL",
)

SYMBOL = "LIVE_TEST/USDT"


@pytest.fixture
def db():
    from data.db import DatabaseConnection

    conn = DatabaseConnection()
    yield conn
    raw = conn.get_connection()
    try:
        with raw.cursor() as cur:
            cur.execute("DELETE FROM prices WHERE symbol = %s", (SYMBOL,))
        raw.commit()
    finally:
        conn.return_connection(raw)
    conn.close_pool()


def _insert_candles(db, timestamps, timeframe="1d", exchange="binance"):
    raw = db.get_connection()
    try:
        with raw.cursor() as cur:
            for ts in timestamps:
                cur.execute(
                    "INSERT INTO prices (exchange, symbol, timeframe, timestamp, open, high, low, close, volume) "
                    "VALUES (%s, %s, %s, %s, 1, 1, 1, 1, 1)",
                    (exchange, SYMBOL, timeframe, ts),
                )
        raw.commit()
    finally:
        db.return_connection(raw)


def test_read_sql_positional_params(db):
    df = db.read_sql("SELECT %s::int AS a, %s::text AS b", [7, "x"])
    assert df.to_dict("records") == [{"a": 7, "b": "x"}]


def test_read_sql_colon_literal_is_not_a_bind_param(db):
    # text() used to parse ':30' as a bind parameter here.
    df = db.read_sql("SELECT '10:30'::text AS t, %s::int AS n", [1])
    assert df.iloc[0]["t"] == "10:30"
    df = db.read_sql("SELECT '10:30'::text AS t")
    assert df.iloc[0]["t"] == "10:30"


def test_read_sql_escaped_percent_with_params(db):
    df = db.read_sql("SELECT ('BTC/USDT' LIKE 'BTC%%') AS hit, %s::int AS n", [1])
    assert bool(df.iloc[0]["hit"]) is True


def test_read_sql_param_count_mismatch_raises(db):
    with pytest.raises(ValueError, match="2 params for 1"):
        db.read_sql("SELECT %s::int AS a", [1, 2])


def test_get_prices_and_find_gaps_against_postgres(db):
    from data.verify_data import find_gaps

    day = datetime(2024, 1, 1, tzinfo=timezone.utc)
    _insert_candles(db, [day, day + timedelta(days=1), day + timedelta(days=3)])
    _insert_candles(db, [day + timedelta(days=2)], timeframe="4h")

    prices = db.get_prices(SYMBOL, timeframe="1d")
    assert len(prices) == 3

    gaps = find_gaps(db, SYMBOL, "1d", date(2024, 1, 1), date(2024, 1, 4))
    assert [g["expected_timestamp"].date() for g in gaps] == [date(2024, 1, 3)]
