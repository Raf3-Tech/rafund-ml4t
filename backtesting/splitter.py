"""Walk-forward validation splitter for RAFund ML4T.

This module provides a reusable time-series splitter that constructs train/test
folds using a rolling, walk-forward window. Each fold's test window begins
immediately after its training window, and test periods are guaranteed not to
overlap.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, List

import pandas as pd
import structlog

from pandas import DateOffset, Timestamp

logger = structlog.get_logger(__name__)


@dataclass
class FoldInfo:
    fold: int
    train_start: Timestamp
    train_end: Timestamp
    test_start: Timestamp
    test_end: Timestamp
    train_df: pd.DataFrame
    test_df: pd.DataFrame
    train_rows: int
    test_rows: int


class TimeSeriesSplitter:
    """Generate walk-forward train/test folds for time-series data."""

    def __init__(
        self,
        train_months: int = 12,
        test_months: int = 3,
        step_months: int = 1,
        min_train_months: int = 6,
    ) -> None:
        self.train_months = train_months
        self.test_months = test_months
        self.step_months = step_months
        self.min_train_months = min_train_months

    def split(self, df: pd.DataFrame, date_col: str = "timestamp") -> list[Dict[str, object]]:
        if date_col not in df.columns:
            raise ValueError(f"DataFrame must contain '{date_col}' column")

        df = df.copy()
        df[date_col] = pd.to_datetime(df[date_col], utc=True)
        df = df.sort_values(date_col).reset_index(drop=True)

        if df.empty:
            return []

        first_date = df[date_col].iloc[0]
        last_date = df[date_col].iloc[-1]
        train_offset = DateOffset(months=self.train_months)
        test_offset = DateOffset(months=self.test_months)
        step_offset = DateOffset(months=self.step_months)

        folds: list[Dict[str, object]] = []
        fold_idx = 0
        fold_start = first_date
        previous_test_end: Timestamp | None = None

        while True:
            train_end = fold_start + train_offset - pd.Timedelta(days=1)
            test_start = train_end + pd.Timedelta(days=1)
            test_end = test_start + test_offset - pd.Timedelta(days=1)

            if test_end > last_date:
                break

            if previous_test_end is not None and test_start <= previous_test_end:
                raise ValueError("Split configuration produces overlapping test periods")

            train_df = df[(df[date_col] >= fold_start) & (df[date_col] <= train_end)]
            test_df = df[(df[date_col] >= test_start) & (df[date_col] <= test_end)]

            if len(train_df) == 0 or len(test_df) == 0:
                break

            actual_train_months = (train_end.to_period("M") - fold_start.to_period("M")).n + 1
            if actual_train_months < self.min_train_months:
                fold_start += step_offset
                continue

            fold_idx += 1
            fold_info = {
                "fold": fold_idx,
                "train_start": fold_start,
                "train_end": train_end,
                "test_start": test_start,
                "test_end": test_end,
                "train_df": train_df,
                "test_df": test_df,
                "train_rows": len(train_df),
                "test_rows": len(test_df),
            }
            folds.append(fold_info)

            logger.info(
                "walk_forward_fold",
                fold=fold_idx,
                train_start=str(fold_start.date()),
                train_end=str(train_end.date()),
                test_start=str(test_start.date()),
                test_end=str(test_end.date()),
                train_rows=len(train_df),
                test_rows=len(test_df),
            )

            previous_test_end = test_end
            next_start = fold_start + step_offset
            if next_start <= test_end:
                fold_start = test_end + pd.Timedelta(days=1)
            else:
                fold_start = next_start

        return folds

    def summary(self, folds: list[Dict[str, object]]) -> pd.DataFrame:
        rows = []
        for fold in folds:
            rows.append(
                {
                    "fold": fold["fold"],
                    "train_start": fold["train_start"].date(),
                    "train_end": fold["train_end"].date(),
                    "test_start": fold["test_start"].date(),
                    "test_end": fold["test_end"].date(),
                    "train_rows": fold["train_rows"],
                    "test_rows": fold["test_rows"],
                }
            )
        return pd.DataFrame(rows)
