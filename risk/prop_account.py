"""Kraken Prop account state — Decimal-exact balance/equity and MDL/MDD floors.

Answers "how much room is left," the same question `trading.position.
max_safe_notional` already answers for the pre-pivot paper/live floor system —
but framed the way Kraken Prop actually enforces it: a daily loss limit
recalculated at 00:30 UTC off the prior day's ending balance (see
daily_clock.py), and a lifetime drawdown floor that never resets.

This module only *describes* the account. It never blocks a trade — that's
risk/pretrade_gate.py (a later phase), the only code allowed to turn the
`daily_soft_triggered`/`daily_hard_triggered`/`lifetime_hard_triggered`
properties below into an actual REDUCE/REJECT decision.

"Our floors sit inside Kraken's": Kraken's own MDL/MDD are the hard,
account-ending breach. DAILY_SOFT_FLOOR_PCT/DAILY_HARD_FLOOR_PCT/
LIFETIME_HARD_FLOOR_FRACTION are deliberately tighter internal trip-wires so
our own code is always what stops us, never the exchange.

Daily thresholds (1.5%, 2.0%) are given in the spec as direct percentages of
balance — the same shape as the existing dollar-amount soft/hard floor in
trading/paper_trader.py ($130/$145 on a $5K account), just Kraken-Prop-scaled
numbers instead. The lifetime threshold (70%) is given as a *fraction of
Kraken's own MDD room* instead, because that room is tier-dependent (3-6%,
not a fixed spec constant like the 3% MDL) — expressing it as a fraction
keeps the trip-wire correct regardless of which tier's kraken_mdd_pct this
account actually has.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

# Kraken Prop's daily loss limit: 3% of balance. Spec fact, not tunable.
KRAKEN_MDL_PCT = Decimal("0.03")

# Our internal daily trip-wires — percentages of daily_start_balance.
DAILY_SOFT_FLOOR_PCT = Decimal("0.015")  # reduce size
DAILY_HARD_FLOOR_PCT = Decimal("0.020")  # flat everything, no new entries until rollover
assert DAILY_SOFT_FLOOR_PCT < DAILY_HARD_FLOOR_PCT < KRAKEN_MDL_PCT

# Our internal lifetime trip-wire — a fraction of Kraken's own (tier-dependent) MDD room.
LIFETIME_HARD_FLOOR_FRACTION = Decimal("0.70")


@dataclass(frozen=True)
class PropAccountState:
    balance: Decimal              # realized equity, no open positions marked
    unrealized_pnl: Decimal       # mark-to-market P&L on currently open positions
    accrued_funding_cost: Decimal  # cumulative funding charged on open positions (>= 0, a cost)
    daily_start_balance: Decimal  # balance snapshot at the last 00:30 UTC rollover
    peak_balance: Decimal         # lifetime high-water mark — never resets
    kraken_mdd_pct: Decimal       # this account's tier MDD room (3-6%) — verified per-tier, never hardcoded
    last_rollover: datetime       # UTC instant of the last 00:30 rollover applied (see daily_clock.py)

    @property
    def equity(self) -> Decimal:
        """Balance plus unrealized P&L, minus funding accrued but not yet
        realized into balance."""
        return self.balance + self.unrealized_pnl - self.accrued_funding_cost

    @property
    def mdl_floor(self) -> Decimal:
        """Equity level at which Kraken's own daily loss limit breaches."""
        return self.daily_start_balance * (1 - KRAKEN_MDL_PCT)

    @property
    def mdd_floor(self) -> Decimal:
        """Equity level at which Kraken's own lifetime drawdown limit breaches."""
        return self.peak_balance * (1 - self.kraken_mdd_pct)

    @property
    def daily_room_remaining(self) -> Decimal:
        """Headroom in equity before Kraken's own MDL breach. Should never go
        negative if our tighter daily_hard_triggered trip-wire is enforced."""
        return self.equity - self.mdl_floor

    @property
    def lifetime_room_remaining(self) -> Decimal:
        """Headroom in equity before Kraken's own MDD breach."""
        return self.equity - self.mdd_floor

    @property
    def daily_loss_pct(self) -> Decimal:
        """Today's loss so far, as a percentage of daily_start_balance. Zero
        (never negative) on a day that's currently up, not down."""
        if self.daily_start_balance <= 0:
            return Decimal("0")
        loss = self.daily_start_balance - self.equity
        return loss / self.daily_start_balance if loss > 0 else Decimal("0")

    @property
    def lifetime_drawdown_fraction(self) -> Decimal:
        """Drawdown from peak_balance, as a fraction of Kraken's own MDD room
        consumed (1.0 == exactly at Kraken's own breach)."""
        if self.kraken_mdd_pct <= 0 or self.peak_balance <= 0:
            return Decimal("0")
        drawdown = self.peak_balance - self.equity
        if drawdown <= 0:
            return Decimal("0")
        return (drawdown / self.peak_balance) / self.kraken_mdd_pct

    @property
    def daily_soft_triggered(self) -> bool:
        return self.daily_loss_pct >= DAILY_SOFT_FLOOR_PCT

    @property
    def daily_hard_triggered(self) -> bool:
        return self.daily_loss_pct >= DAILY_HARD_FLOOR_PCT

    @property
    def lifetime_hard_triggered(self) -> bool:
        return self.lifetime_drawdown_fraction >= LIFETIME_HARD_FLOOR_FRACTION


def from_position_state(
    pos,
    *,
    kraken_mdd_pct: Decimal,
    last_rollover: datetime,
    unrealized_pnl: Decimal = Decimal("0"),
    accrued_funding_cost: Decimal = Decimal("0"),
) -> PropAccountState:
    """Build a PropAccountState from the existing trading.position.PositionState
    rather than a second, parallel account model. `pos.equity`/`pos.peak_equity`
    are floats (DB-backed) — converted via str() so no binary-float artifact
    leaks into the Decimal risk path."""
    return PropAccountState(
        balance=Decimal(str(pos.equity)),
        unrealized_pnl=unrealized_pnl,
        accrued_funding_cost=accrued_funding_cost,
        daily_start_balance=Decimal(str(pos.daily_start_equity)),
        peak_balance=Decimal(str(pos.peak_equity)),
        kraken_mdd_pct=kraken_mdd_pct,
        last_rollover=last_rollover,
    )
