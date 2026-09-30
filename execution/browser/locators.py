"""Centralized, named locators for the Kraken Pro/Prop trading terminal.

PLACEHOLDERS — READ BEFORE TRUSTING ANY VALUE BELOW: this session has never
had access to Kraken's live terminal DOM or accessibility tree. Every
role/name pair here is a best-guess based on common trading-UI patterns,
not a verified selector. Before execution/browser/kraken_terminal.py can
observe anything real, a human must open the actual Kraken Pro/Prop
terminal, use Playwright's inspector/codegen (or an MCP browser tool's
accessibility snapshot — AGENT_ORCHESTRATION.md Section 12) to find the
real role/name or test-id for each element, and correct every entry here.

Centralizing them in one file means that correction — or a future Kraken UI
change — touches this file once, not every observe_* method in
kraken_terminal.py.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RoleLocator:
    role: str
    name: str


ACCOUNT_SELECTOR = RoleLocator(role="button", name="Account selector")
# Substring expected inside the Prop account's own label/name text —
# distinguishes it from a spot/margin account sharing the same UI.
PROP_ACCOUNT_LABEL_MARKER = "Prop"

BALANCE = RoleLocator(role="status", name="Balance")
EQUITY = RoleLocator(role="status", name="Equity")
DAILY_LOSS_LIMIT = RoleLocator(role="status", name="Daily loss limit")
DRAWDOWN_LIMIT = RoleLocator(role="status", name="Drawdown limit")

POSITIONS_TABLE = RoleLocator(role="table", name="Open positions")
ORDERS_TABLE = RoleLocator(role="table", name="Open orders")

BEST_BID = RoleLocator(role="status", name="Best bid")
BEST_ASK = RoleLocator(role="status", name="Best ask")
LAST_PRICE = RoleLocator(role="status", name="Last price")

ORDER_FORM = RoleLocator(role="form", name="Order entry")
ORDER_FORM_SYMBOL = RoleLocator(role="combobox", name="Symbol")
ORDER_FORM_SIDE = RoleLocator(role="radiogroup", name="Side")
ORDER_FORM_TYPE = RoleLocator(role="combobox", name="Order type")
ORDER_FORM_PRICE = RoleLocator(role="spinbutton", name="Price")
ORDER_FORM_QUANTITY = RoleLocator(role="spinbutton", name="Quantity")

CONFIRMATION_DIALOG = RoleLocator(role="dialog", name="Order confirmation")
