from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Tuple, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from config.logging_config import get_logger

logger = get_logger(__name__)

Timeframe = Literal['1m', '5m', '15m', '1h', '1d']


class OHLCVRecord(BaseModel):
    symbol: str
    timeframe: Timeframe
    timestamp: datetime
    open: float = Field(..., gt=0)
    high: float = Field(..., gt=0)
    low: float = Field(..., gt=0)
    close: float = Field(..., gt=0)
    volume: float = Field(..., ge=0)

    @model_validator(mode='after')
    def validate_price_relationships(self) -> 'OHLCVRecord':
        if self.high < self.low:
            raise ValueError('high must be greater than or equal to low')
        if self.high < self.open:
            raise ValueError('high must be greater than or equal to open')
        if self.high < self.close:
            raise ValueError('high must be greater than or equal to close')
        if self.low > self.open:
            raise ValueError('low must be less than or equal to open')
        if self.low > self.close:
            raise ValueError('low must be less than or equal to close')
        return self

    @field_validator('timestamp')
    def ensure_utc_and_not_future(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        else:
            value = value.astimezone(timezone.utc)

        if value > datetime.now(timezone.utc):
            raise ValueError('timestamp must not be in the future')
        return value


class CollectionResult(BaseModel):
    symbol: str
    timeframe: Timeframe
    records_fetched: int
    records_inserted: int
    records_rejected: int
    errors: List[str]
    started_at: datetime
    completed_at: datetime


def validate_ohlcv_batch(raw_rows: list[dict]) -> tuple[list[OHLCVRecord], list[str]]:
    valid_records: list[OHLCVRecord] = []
    errors: list[str] = []

    for index, raw_row in enumerate(raw_rows, start=1):
        try:
            record = OHLCVRecord(**raw_row)
            valid_records.append(record)
        except ValidationError as exc:
            for issue in exc.errors():
                message = (
                    f"Validation failed for row {index}: symbol={raw_row.get('symbol')} "
                    f"timeframe={raw_row.get('timeframe')} error={issue.get('msg')}"
                )
                logger.warning(message)
                errors.append(message)

    return valid_records, errors
