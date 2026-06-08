"""Triple-barrier labeling (López de Prado, *Advances in Financial ML*, ch. 3).

Each observation is labeled by which of three barriers its forward path touches
first within a holding horizon:

    +1  upper barrier (take-profit) hit first
    -1  lower barrier (stop-loss)  hit first
     0  neither hit within the horizon (the vertical / time barrier)

Barriers are placed in units of *local volatility* so they self-scale with the
regime:

    upper return threshold = +tp_mult * vol[t]
    lower return threshold = -sl_mult * vol[t]

where ``vol[t]`` is a causal EWMA of returns known at time ``t``.

Direction of time
-----------------
The label at ``t`` looks **forward** over ``[t+1, t+horizon]``. That is correct:
it is a supervised-learning *target*, not a feature. Look-ahead is prevented at
the model level by dropping rows whose horizon spills past the end of the
training fold (``truncated == True``), so no training row peeks beyond its fold.

Everything else in this module (the volatility estimate, and any feature derived
from it) is strictly causal.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

DEFAULT_VOL_SPAN = 20
DEFAULT_HORIZON = 10
DEFAULT_TP_MULT = 2.0
DEFAULT_SL_MULT = 2.0


def ewma_volatility(prices: pd.Series, span: int = DEFAULT_VOL_SPAN) -> pd.Series:
    """Causal EWMA volatility of simple returns.

    ``vol[t]`` uses only returns up to and including ``t`` (an exponentially
    weighted std), so it is safe to use as the barrier width for the event that
    *opens* at ``t``.
    """
    prices = pd.Series(prices, dtype="float64")
    returns = prices.pct_change()
    return returns.ewm(span=span, min_periods=span).std()


@dataclass
class BarrierConfig:
    """Parameters that define the triple barrier (the GA search space)."""

    tp_mult: float = DEFAULT_TP_MULT
    sl_mult: float = DEFAULT_SL_MULT
    horizon: int = DEFAULT_HORIZON
    vol_span: int = DEFAULT_VOL_SPAN

    def validate(self) -> "BarrierConfig":
        if self.tp_mult <= 0 or self.sl_mult <= 0:
            raise ValueError("tp_mult and sl_mult must be > 0")
        if self.horizon < 1:
            raise ValueError("horizon must be >= 1")
        if self.vol_span < 2:
            raise ValueError("vol_span must be >= 2")
        return self


def triple_barrier_labels(
    prices: pd.Series,
    config: BarrierConfig | None = None,
    *,
    vol: pd.Series | None = None,
) -> pd.DataFrame:
    """Label every row of ``prices`` by the triple-barrier method.

    Args:
        prices: positive price (or price-like) series, time-ordered.
        config: barrier parameters; defaults to :class:`BarrierConfig` defaults.
        vol: optional precomputed causal volatility (e.g. shared across a GA
            sweep to avoid recomputation). When omitted it is computed via
            :func:`ewma_volatility`.

    Returns:
        DataFrame indexed like ``prices`` with columns:
          * ``label``      - {+1, -1, 0} or NaN during warm-up (no vol yet)
          * ``barrier``    - {'tp','sl','time'} or NaN
          * ``ret``        - realized return from entry to the exit bar
          * ``exit_offset``- bars from entry to the touch (>=1), or NaN
          * ``truncated``  - True if the horizon ran past the series end
    """
    cfg = (config or BarrierConfig()).validate()
    prices = pd.Series(prices, dtype="float64")
    n = len(prices)
    px = prices.to_numpy(dtype=float)

    if vol is None:
        vol = ewma_volatility(prices, span=cfg.vol_span)
    vol_arr = pd.Series(vol, dtype="float64").to_numpy(dtype=float)

    label = np.full(n, np.nan)
    barrier = np.array([None] * n, dtype=object)
    ret = np.full(n, np.nan)
    exit_offset = np.full(n, np.nan)
    truncated = np.zeros(n, dtype=bool)

    for t in range(n):
        v = vol_arr[t]
        entry = px[t]
        if not np.isfinite(v) or v <= 0 or not np.isfinite(entry) or entry <= 0:
            continue  # warm-up / bad data: leave NaN

        up = cfg.tp_mult * v
        dn = -cfg.sl_mult * v
        last = min(t + cfg.horizon, n - 1)
        truncated[t] = (t + cfg.horizon) > (n - 1)

        hit_label, hit_barrier, hit_ret, hit_off = 0, "time", np.nan, np.nan
        for s in range(t + 1, last + 1):
            ps = px[s]
            if not np.isfinite(ps):
                continue
            r = ps / entry - 1.0
            if r >= up:
                hit_label, hit_barrier, hit_ret, hit_off = 1, "tp", r, s - t
                break
            if r <= dn:
                hit_label, hit_barrier, hit_ret, hit_off = -1, "sl", r, s - t
                break
        else:
            # No barrier touched: realized return is to the (truncated) horizon.
            if last > t and np.isfinite(px[last]):
                hit_ret = px[last] / entry - 1.0
                hit_off = last - t

        label[t] = hit_label
        barrier[t] = hit_barrier
        ret[t] = hit_ret
        exit_offset[t] = hit_off

    return pd.DataFrame(
        {
            "label": label,
            "barrier": barrier,
            "ret": ret,
            "exit_offset": exit_offset,
            "truncated": truncated,
        },
        index=prices.index,
    )


def drop_unusable_labels(labels: pd.DataFrame) -> pd.DataFrame:
    """Keep only rows safe for training: a real label and a complete horizon.

    Drops warm-up rows (NaN label) and rows whose forward window was truncated by
    the end of the fold/series (those would otherwise leak fold-boundary effects
    or carry an incomplete outcome).
    """
    mask = labels["label"].notna() & (~labels["truncated"])
    return labels.loc[mask]
