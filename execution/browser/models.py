"""Observed-state data models — AGENT_ORCHESTRATION.md Section 52's
checklist (account selector, Prop account, balance, equity, risk limits,
open positions, open orders, market, order book, order form, order status,
confirmation state), mapped onto the fewest dataclasses that cover all of
it without one class per bullet point:

  account selector, Prop account, balance, equity, risk limits -> ObservedAccountState
  open positions                                                -> ObservedPosition
  open orders, order status                                      -> ObservedOrder
  market, order book                                              -> ObservedMarket
  order form                                                      -> ObservedOrderForm
  confirmation state                                              -> ObservedConfirmationState

Every field is Optional where a terminal element might not have rendered
yet — a missing value is an honest "not observed," never defaulted to zero
or an empty string that could be misread as a real reading of zero.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class ObservedAccountState:
    account_label: str
    is_prop_account: bool
    balance: Optional[Decimal]
    equity: Optional[Decimal]
    daily_loss_limit: Optional[Decimal]
    drawdown_limit: Optional[Decimal]
    observed_at: datetime


@dataclass(frozen=True)
class ObservedPosition:
    symbol: str
    side: str  # LONG | SHORT
    quantity: Optional[Decimal]
    entry_price: Optional[Decimal]
    unrealized_pnl: Optional[Decimal]
    observed_at: datetime


@dataclass(frozen=True)
class ObservedOrder:
    order_id: str
    symbol: str
    side: str
    order_type: str
    price: Optional[Decimal]
    quantity: Optional[Decimal]
    status: str
    observed_at: datetime


@dataclass(frozen=True)
class ObservedMarket:
    """Top-of-book only, deliberately — this system places limit orders at
    the signal bar's close (AGENT_ORCHESTRATION.md Section 16) and never
    chases the book, so full depth was never needed to support that
    doctrine."""
    symbol: str
    best_bid: Optional[Decimal]
    best_ask: Optional[Decimal]
    last_price: Optional[Decimal]
    observed_at: datetime


@dataclass(frozen=True)
class ObservedOrderForm:
    """Whatever is currently populated in the terminal's order-entry
    form — read-only. A later phase's Supervised Browser mode (Section 43)
    compares this against an ExecutionIntent before a human authorizes
    submission; nothing in this class, or anywhere in execution/browser/,
    submits it."""
    symbol: Optional[str]
    side: Optional[str]
    order_type: Optional[str]
    price: Optional[Decimal]
    quantity: Optional[Decimal]
    observed_at: datetime


@dataclass(frozen=True)
class ObservedConfirmationState:
    visible: bool
    message: Optional[str]
    observed_at: datetime
