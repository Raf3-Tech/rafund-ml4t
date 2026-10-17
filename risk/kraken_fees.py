"""Kraken Prop fee schedule and fee tracker — the single source of truth for
what a Kraken Prop account is charged, used by risk math, backtests, paper
trading and simulations.

Sources (verified 2026-10-17):
  - https://support.kraken.com/articles/kraken-prop-faq
  - https://support.kraken.com/articles/kraken-prop-plans-and-pricing
  - https://support.kraken.com/articles/how-kraken-prop-evaluations-work

What Kraken states:
  - Commission: 4 bps (0.04%) per side of every trade. No maker/taker split.
  - Margin funding: 0.033% per day on open positions, long and short,
    charged every 4 hours.
  - "All fees reduce your account balance, which counts towards your maximum
    daily loss calculations and MDD/MDL equity limits."
  - MDL: 3% of the balance at 00:30 UTC; MDD (Advanced): 3% static from the
    starting balance. No time limit.

What Kraken does NOT state (assumptions, kept explicit and overridable):
  - The wall-clock times of the 4-hour funding charges. `ChargeTiming.CLOCK`
    assumes charges at 00:00/04:00/.../20:00 UTC on whatever is open at that
    instant; `ChargeTiming.BLOCKS` charges every started 4h block of the hold
    (the conservative rule `risk.cost_model` has always used).
  - Whether funding accrues on notional or on borrowed amount. Notional is
    assumed (conservative).
  - Slippage and spread are not fees and are not modelled here; pass them
    separately to whatever consumes this schedule.

If Kraken changes the schedule, change it here only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional

import pandas as pd


class ChargeTiming(str, Enum):
    CLOCK = "clock"    # charged at fixed UTC 4h marks while the position is open
    BLOCKS = "blocks"  # every started 4h block of the hold pays a full block


@dataclass(frozen=True)
class KrakenPropFeeSchedule:
    commission_per_side: Decimal = Decimal("0.0004")
    funding_per_day: Decimal = Decimal("0.00033")
    funding_interval_hours: int = 4
    verified_on: str = "2026-10-17"
    sources: tuple = (
        "https://support.kraken.com/articles/kraken-prop-faq",
        "https://support.kraken.com/articles/kraken-prop-plans-and-pricing",
        "https://support.kraken.com/articles/how-kraken-prop-evaluations-work",
    )

    @property
    def funding_charges_per_day(self) -> int:
        return 24 // self.funding_interval_hours

    @property
    def funding_per_charge(self) -> Decimal:
        return self.funding_per_day / self.funding_charges_per_day

    # ── float views for backtests / simulations ──────────────────────────
    @property
    def commission_rate(self) -> float:
        return float(self.commission_per_side)

    def funding_rate_for_hours(self, hours: float) -> float:
        """Average funding as a fraction of notional for `hours` of holding,
        for bar-based engines that charge per bar instead of per 4h mark."""
        return float(self.funding_per_day) * hours / 24.0

    # ── exact charges ─────────────────────────────────────────────────────
    def commission(self, notional: float) -> float:
        return abs(notional) * self.commission_rate

    def funding_charge_count(
        self,
        opened_at: datetime,
        closed_at: datetime,
        timing: ChargeTiming = ChargeTiming.CLOCK,
    ) -> int:
        opened_at, closed_at = _utc(opened_at), _utc(closed_at)
        if closed_at <= opened_at:
            return 0
        step = timedelta(hours=self.funding_interval_hours)
        if timing is ChargeTiming.BLOCKS:
            return math.ceil((closed_at - opened_at) / step)
        # CLOCK: count the 4h UTC marks in (opened_at, closed_at].
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        first = math.floor((opened_at - epoch) / step) + 1
        last = math.floor((closed_at - epoch) / step)
        return max(0, last - first + 1)

    def funding(
        self,
        notional: float,
        opened_at: datetime,
        closed_at: datetime,
        timing: ChargeTiming = ChargeTiming.CLOCK,
    ) -> float:
        n = self.funding_charge_count(opened_at, closed_at, timing)
        return abs(notional) * float(self.funding_per_charge) * n

    def round_trip_cost(
        self, notional: float, hold_hours: float = 0.0, timing: ChargeTiming = ChargeTiming.BLOCKS
    ) -> float:
        """Entry + exit commission plus funding for a hold of `hold_hours`.
        Defaults to BLOCKS timing (worst case) for pre-trade estimates."""
        start = datetime(2000, 1, 1, tzinfo=timezone.utc)
        return 2 * self.commission(notional) + self.funding(
            notional, start, start + timedelta(hours=hold_hours), timing
        )

    def cost_in_r(self, stop_distance_pct: float, hold_hours: float = 0.0) -> float:
        """Fee drag per trade in units of R, for a position sized so the
        stop loses 1R: notional = R / stop_pct, so cost/R = cost_pct / stop_pct.
        E.g. a 2% stop held 1 day costs (0.08% + 0.033%) / 2% = 0.057R."""
        if stop_distance_pct <= 0:
            raise ValueError("stop_distance_pct must be positive")
        return self.round_trip_cost(1.0, hold_hours) / stop_distance_pct


KRAKEN_PROP_FEES = KrakenPropFeeSchedule()


def _utc(ts) -> datetime:
    ts = pd.Timestamp(ts)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    return ts.to_pydatetime()


@dataclass
class FeeEvent:
    trade_id: str
    kind: str          # "commission_open" | "commission_close" | "funding"
    at: datetime
    amount: float      # dollars charged (positive = cost)
    notional: float


@dataclass
class _OpenTrade:
    trade_id: str
    notional: float
    opened_at: datetime
    funded_until: datetime


class KrakenFeeTracker:
    """Ledger of every fee a simulated or journaled Kraken Prop account pays.

    Feed it fills and the passage of time; it records each commission and
    each 4-hourly funding charge as a FeeEvent, so a simulation can deduct
    them from the balance at the moment Kraken would (fees count toward the
    MDL/MDD), and a journal can reconcile them against Kraken's history.

        tracker = KrakenFeeTracker()
        tracker.open("t1", notional=1250, at=t0)        # -> commission
        tracker.accrue(now)                             # -> funding marks passed
        tracker.close("t1", notional=1262, at=t1)       # -> funding + commission
        tracker.total(), tracker.by_trade(), tracker.to_frame()
    """

    def __init__(
        self,
        schedule: KrakenPropFeeSchedule = KRAKEN_PROP_FEES,
        timing: ChargeTiming = ChargeTiming.CLOCK,
    ) -> None:
        self.schedule = schedule
        self.timing = timing
        self.events: List[FeeEvent] = []
        self._open: Dict[str, _OpenTrade] = {}

    def open(self, trade_id: str, notional: float, at) -> float:
        if trade_id in self._open:
            raise ValueError(f"trade {trade_id} is already open")
        at = _utc(at)
        self._open[trade_id] = _OpenTrade(trade_id, abs(notional), at, at)
        return self._record(trade_id, "commission_open", at, self.schedule.commission(notional), notional)

    def accrue(self, now) -> float:
        """Charge funding for every open trade up to `now`; returns dollars charged."""
        now = _utc(now)
        charged = 0.0
        for t in self._open.values():
            charged += self._fund(t, now)
        return charged

    def close(self, trade_id: str, notional: float, at) -> float:
        """Charge outstanding funding plus exit commission; returns dollars charged."""
        at = _utc(at)
        t = self._open.pop(trade_id)
        charged = self._fund(t, at)
        charged += self._record(trade_id, "commission_close", at, self.schedule.commission(notional), notional)
        return charged

    @property
    def open_trades(self) -> List[str]:
        return list(self._open)

    def total(self, kind: Optional[str] = None) -> float:
        return sum(e.amount for e in self.events if kind is None or e.kind == kind)

    def by_trade(self) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for e in self.events:
            out[e.trade_id] = out.get(e.trade_id, 0.0) + e.amount
        return out

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame([e.__dict__ for e in self.events],
                            columns=["trade_id", "kind", "at", "amount", "notional"])

    def _fund(self, t: _OpenTrade, until: datetime) -> float:
        n = self.schedule.funding_charge_count(t.funded_until, until, self.timing)
        if self.timing is ChargeTiming.BLOCKS:
            # Blocks are counted from the open; never re-charge a started block.
            total_blocks = self.schedule.funding_charge_count(t.opened_at, until, ChargeTiming.BLOCKS)
            paid_blocks = self.schedule.funding_charge_count(t.opened_at, t.funded_until, ChargeTiming.BLOCKS)
            n = max(0, total_blocks - paid_blocks)
        if n <= 0 or until <= t.funded_until:
            return 0.0
        amount = t.notional * float(self.schedule.funding_per_charge) * n
        t.funded_until = until
        return self._record(t.trade_id, "funding", until, amount, t.notional)

    def _record(self, trade_id, kind, at, amount, notional) -> float:
        self.events.append(FeeEvent(trade_id, kind, at, float(amount), abs(float(notional))))
        return float(amount)
