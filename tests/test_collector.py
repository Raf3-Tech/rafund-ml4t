from datetime import datetime, timezone
from unittest.mock import MagicMock

import ccxt
import pytest

from data.collectors.binance_collector import BinanceCollector


def make_candle(ts: datetime) -> list:
    return [int(ts.timestamp() * 1000), 40000.0, 41000.0, 39000.0, 40500.0, 1000.0]


def test_fetch_ohlcv_safe_retries_on_network_error(monkeypatch):
    collector = BinanceCollector(rate_limit_ms=0)
    collector._respect_rate_limit = MagicMock()
    collector.client = MagicMock()

    collector.client.fetch_ohlcv.side_effect = [
        ccxt.NetworkError('temporary network issue'),
        [make_candle(datetime(2024, 1, 1, tzinfo=timezone.utc))],
    ]

    rows, errors = collector.fetch_ohlcv_safe('BTC/USDT', '1d', limit=1)

    assert len(rows) == 1
    assert rows[0]['symbol'] == 'BTC/USDT'
    assert rows[0]['timestamp'] == datetime(2024, 1, 1, tzinfo=timezone.utc)
    assert errors == []
    assert collector.client.fetch_ohlcv.call_count == 2


def test_fetch_ohlcv_safe_logs_and_skips_invalid_rows(monkeypatch):
    collector = BinanceCollector(rate_limit_ms=0)
    collector._respect_rate_limit = MagicMock()
    collector._fetch_ohlcv = MagicMock(return_value=[
        make_candle(datetime(2024, 1, 1, tzinfo=timezone.utc)),
        [int(datetime(2024, 1, 2, tzinfo=timezone.utc).timestamp() * 1000), 40000.0, 38000.0, 39000.0, 38500.0, 1000.0],
    ])

    rows, errors = collector.fetch_ohlcv_safe('BTC/USDT', '1d', limit=2)
    assert len(rows) == 1
    assert len(errors) == 1
    assert 'high must be greater than or equal to low' in errors[0]


def test_collect_symbol_stops_at_since_date(monkeypatch):
    collector = BinanceCollector(rate_limit_ms=0)
    collector._respect_rate_limit = MagicMock()

    # return two batches, then empty to stop
    first = [make_candle(datetime(2024, 1, 1, tzinfo=timezone.utc))]
    second = [make_candle(datetime(2024, 1, 2, tzinfo=timezone.utc))]
    collector._fetch_ohlcv = MagicMock(side_effect=[first, second, []])

    result = collector.collect_symbol(
        symbol='BTC/USDT',
        timeframe='1d',
        from_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        to_date=datetime(2024, 1, 2, tzinfo=timezone.utc),
    )

    assert result.records_fetched == 2
    assert result.records_inserted == 2
    assert result.records_rejected == 0
    assert len(collector.last_collection_df) == 2
    assert collector._fetch_ohlcv.call_count == 2
