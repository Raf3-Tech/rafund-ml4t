"""Shared JSON-serialization helpers for dashboard/publish routes."""
from __future__ import annotations

import pandas as pd


def records(df) -> list[dict]:
    """df.to_dict(orient="records"), with NaN (pandas' rendering of SQL NULL
    in numeric columns, e.g. pnl on OPEN rows) swapped for None — NaN is not
    valid JSON, so jsonify() would emit a bare `NaN` token that browsers'
    strict JSON parser rejects outright."""
    # astype(object) first — df.where(..., None) on a still-numeric column
    # just re-coerces None back to NaN rather than actually storing None.
    return df.astype(object).where(pd.notnull(df), None).to_dict(orient="records")
