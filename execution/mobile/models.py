"""Observed-state data models for the Kraken Pro Android app's Prop tab.

Field names follow the app's own on-screen labels (Total equity, Target
equity, Daily loss limit, Drawdown limit — see
docs/mobile_nav/kraken_app/NAV_TREE.md) rather than this codebase's
internal risk/prop_account.py naming, since these are two different things
even where the numbers happen to coincide: the app's "Daily loss limit"/
"Drawdown limit" values are Kraken's own displayed floor levels (in USD),
not this system's independently-computed, deliberately tighter internal
trip-wires (risk/prop_account.py's DAILY_HARD_FLOOR_PCT etc.). Comparing
the two is exactly the kind of ACCOUNT_STATE_DISAGREEMENT check
senior_trader/escalation.py's categories anticipate — not implemented by
this phase, which only observes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import List, Optional


@dataclass(frozen=True)
class ObservedFavorite:
    symbol: str
    leverage: str
    price: Optional[Decimal]
    name: str
    change_pct: Optional[Decimal]


@dataclass(frozen=True)
class ObservedPositionsPanel:
    """Positions and Orders share one panel with two sub-tabs in the app;
    only the active sub-tab's content is on screen at once. `positions`/
    `orders` are populated-row parsing that has never been exercised
    against real data (every capture so far showed the empty state) — see
    parser.py's module docstring for what's actually implemented today."""
    active_tab: Optional[str]  # "Positions" | "Orders" | None if undetermined
    empty_message: Optional[str]  # e.g. "No position entries available"
    positions: List[dict] = field(default_factory=list)
    orders: List[dict] = field(default_factory=list)


@dataclass(frozen=True)
class ObservedPropAccountScreen:
    """One parsed snapshot of the Prop tab's home screen."""
    account_label: str
    total_equity: Optional[Decimal]
    total_equity_change: Optional[str]  # raw text, e.g. "+0.00 USD (+0.00%)" — not split further, display-only
    target_equity: Optional[Decimal]
    daily_loss_limit: Optional[Decimal]
    drawdown_limit: Optional[Decimal]
    favorites: List[ObservedFavorite]
    positions_panel: ObservedPositionsPanel
    observed_at: datetime
