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
class ObservedTradeForm:
    """One parsed snapshot of the Trade tab's order-entry form (Buy or
    Sell, Market/Limit/Trigger). Derived from a REAL captured dump
    (2026-09-30, live device, content-verified via dump_ui_stable's
    expect_contains and cross-checked against a screenshot taken at the
    same moment — not guessed), same discipline as parser.py's Prop-tab
    parsing. See parser.py's module docstring for the one caveat: which
    side (Buy/Sell) is currently selected is not derivable from this
    dump's attributes alone (no visual/selected signal was found in the
    captured tree) — `side` is None until a future capture finds one."""
    side: Optional[str]  # "Buy" | "Sell" | None — see caveat above
    order_type: Optional[str]  # "Market" | "Limit" | "Trigger" | None
    price: Optional[Decimal]  # Limit price USD, or Market price USD when order_type == "Market"
    quantity: Optional[Decimal]  # Quantity (BTC)
    total_usd: Optional[Decimal]
    available_usd: Optional[Decimal]
    tp_sl_enabled: bool
    take_profit_price: Optional[Decimal]  # only present in the dump when tp_sl_enabled
    stop_loss_price: Optional[Decimal]  # only present in the dump when tp_sl_enabled
    submit_button_label: Optional[str]  # e.g. "Buy BTC" — presence/label only, not a claim about tappability
    observed_at: datetime


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
