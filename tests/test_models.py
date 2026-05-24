from datetime import datetime, timezone, timedelta

import pytest
from pydantic import ValidationError

from data.models import OHLCVRecord, validate_ohlcv_batch


def test_ohlcv_record_valid():
    record = OHLCVRecord(
        symbol='BTC/USDT',
        timeframe='1d',
        timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
        open=40000.0,
        high=41000.0,
        low=39000.0,
        close=40500.0,
        volume=1000.0,
    )

    assert record.symbol == 'BTC/USDT'
    assert record.timeframe == '1d'
    assert record.high >= record.low


def test_ohlcv_record_rejects_high_below_low():
    with pytest.raises(ValidationError):
        OHLCVRecord(
            symbol='BTC/USDT',
            timeframe='1d',
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            open=40000.0,
            high=38000.0,
            low=39000.0,
            close=38500.0,
            volume=1000.0,
        )


def test_ohlcv_record_rejects_future_timestamp():
    future_time = datetime.now(timezone.utc) + timedelta(days=1)
    with pytest.raises(ValidationError):
        OHLCVRecord(
            symbol='BTC/USDT',
            timeframe='1d',
            timestamp=future_time,
            open=40000.0,
            high=41000.0,
            low=39000.0,
            close=40500.0,
            volume=1000.0,
        )


def test_ohlcv_record_rejects_negative_volume():
    with pytest.raises(ValidationError):
        OHLCVRecord(
            symbol='BTC/USDT',
            timeframe='1d',
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            open=40000.0,
            high=41000.0,
            low=39000.0,
            close=40500.0,
            volume=-1.0,
        )


def test_validate_ohlcv_batch_mixed_rows():
    raw_rows = [
        {
            'symbol': 'BTC/USDT',
            'timeframe': '1d',
            'timestamp': datetime(2024, 1, 1, tzinfo=timezone.utc),
            'open': 40000.0,
            'high': 41000.0,
            'low': 39000.0,
            'close': 40500.0,
            'volume': 1000.0,
        },
        {
            'symbol': 'BTC/USDT',
            'timeframe': '1d',
            'timestamp': datetime(2024, 1, 2, tzinfo=timezone.utc),
            'open': 40000.0,
            'high': 38000.0,
            'low': 39000.0,
            'close': 38500.0,
            'volume': 1000.0,
        },
    ]

    valid_records, errors = validate_ohlcv_batch(raw_rows)
    assert len(valid_records) == 1
    assert len(errors) == 1
    assert 'high must be greater than or equal to low' in errors[0]
