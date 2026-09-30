"""Read-only Kraken terminal observer — AGENT_ORCHESTRATION.md Section 52
(Phase 4).

Every method here OBSERVES. None of them can submit, cancel, or modify an
order — that capability does not exist in this class at all, not even
disabled: there is no submit/place/cancel/modify method anywhere below.
tests/test_execution_browser_boundaries.py asserts this structurally (scans
the class's actual method names) rather than trusting this docstring alone.

Follows AGENT_ORCHESTRATION.md Section 12's interaction loop — snapshot,
identify element, read, done — using named role-based locators from
execution/browser/locators.py rather than inline selector strings, so a
locator fix touches one file. Those locators are UNVERIFIED PLACEHOLDERS
(see that module's docstring) — the parsing/mapping logic below is real
and tested; the exact selectors are not, and will not work against the
real Kraken terminal until a human corrects them against the live DOM.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, List, Optional

from execution.browser import locators
from execution.browser.models import (
    ObservedAccountState,
    ObservedConfirmationState,
    ObservedMarket,
    ObservedOrder,
    ObservedOrderForm,
    ObservedPosition,
)

if TYPE_CHECKING:
    from playwright.sync_api import Page


def _parse_money(text: Optional[str]) -> Optional[Decimal]:
    """Strips currency symbols/commas/whitespace from a terminal-displayed
    amount. Returns None for empty or placeholder text (e.g. "--") rather
    than raising or defaulting to zero — a field that hasn't rendered yet
    is a legitimate observed state, not an error and not a real zero."""
    if not text:
        return None
    cleaned = text.strip().replace(",", "").replace("$", "").replace("USD", "").strip()
    if cleaned in ("", "--", "-", "N/A"):
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


class KrakenTerminalObserver:
    """Wraps one Playwright Page already navigated to (and, by the time
    any method here is called, logged into) the Kraken Pro/Prop terminal.
    Construction does not itself launch a browser or navigate anywhere —
    see execution/browser/browser_manager.py::kraken_terminal_session for
    that, which enforces the compliance gate before a Page ever exists."""

    def __init__(self, page: "Page") -> None:
        self._page = page

    def observe_account_state(self) -> ObservedAccountState:
        label = self._page.get_by_role(locators.ACCOUNT_SELECTOR.role, name=locators.ACCOUNT_SELECTOR.name).inner_text()
        balance_text = self._page.get_by_role(locators.BALANCE.role, name=locators.BALANCE.name).inner_text()
        equity_text = self._page.get_by_role(locators.EQUITY.role, name=locators.EQUITY.name).inner_text()
        daily_loss_text = self._page.get_by_role(
            locators.DAILY_LOSS_LIMIT.role, name=locators.DAILY_LOSS_LIMIT.name
        ).inner_text()
        drawdown_text = self._page.get_by_role(
            locators.DRAWDOWN_LIMIT.role, name=locators.DRAWDOWN_LIMIT.name
        ).inner_text()

        return ObservedAccountState(
            account_label=(label or "").strip(),
            is_prop_account=locators.PROP_ACCOUNT_LABEL_MARKER in (label or ""),
            balance=_parse_money(balance_text),
            equity=_parse_money(equity_text),
            daily_loss_limit=_parse_money(daily_loss_text),
            drawdown_limit=_parse_money(drawdown_text),
            observed_at=datetime.now(timezone.utc),
        )

    def observe_positions(self) -> List[ObservedPosition]:
        rows = self._page.get_by_role(locators.POSITIONS_TABLE.role, name=locators.POSITIONS_TABLE.name).locator(
            "tbody tr"
        ).all()
        now = datetime.now(timezone.utc)
        positions = []
        for row in rows:
            cells = row.locator("td").all_inner_texts()
            if len(cells) < 5:
                continue
            symbol, side, qty_text, entry_text, pnl_text = cells[:5]
            positions.append(ObservedPosition(
                symbol=symbol.strip(), side=side.strip().upper(),
                quantity=_parse_money(qty_text), entry_price=_parse_money(entry_text),
                unrealized_pnl=_parse_money(pnl_text), observed_at=now,
            ))
        return positions

    def observe_open_orders(self) -> List[ObservedOrder]:
        rows = self._page.get_by_role(locators.ORDERS_TABLE.role, name=locators.ORDERS_TABLE.name).locator(
            "tbody tr"
        ).all()
        now = datetime.now(timezone.utc)
        orders = []
        for row in rows:
            cells = row.locator("td").all_inner_texts()
            if len(cells) < 7:
                continue
            order_id, symbol, side, order_type, price_text, qty_text, status = cells[:7]
            orders.append(ObservedOrder(
                order_id=order_id.strip(), symbol=symbol.strip(), side=side.strip().upper(),
                order_type=order_type.strip().lower(), price=_parse_money(price_text),
                quantity=_parse_money(qty_text), status=status.strip().lower(), observed_at=now,
            ))
        return orders

    def observe_market(self, symbol: str) -> ObservedMarket:
        bid_text = self._page.get_by_role(locators.BEST_BID.role, name=locators.BEST_BID.name).inner_text()
        ask_text = self._page.get_by_role(locators.BEST_ASK.role, name=locators.BEST_ASK.name).inner_text()
        last_text = self._page.get_by_role(locators.LAST_PRICE.role, name=locators.LAST_PRICE.name).inner_text()
        return ObservedMarket(
            symbol=symbol, best_bid=_parse_money(bid_text), best_ask=_parse_money(ask_text),
            last_price=_parse_money(last_text), observed_at=datetime.now(timezone.utc),
        )

    def observe_order_form(self) -> ObservedOrderForm:
        symbol_text = self._page.get_by_role(locators.ORDER_FORM_SYMBOL.role, name=locators.ORDER_FORM_SYMBOL.name).input_value()
        side_text = self._page.get_by_role(locators.ORDER_FORM_SIDE.role, name=locators.ORDER_FORM_SIDE.name).inner_text()
        type_text = self._page.get_by_role(locators.ORDER_FORM_TYPE.role, name=locators.ORDER_FORM_TYPE.name).input_value()
        price_text = self._page.get_by_role(locators.ORDER_FORM_PRICE.role, name=locators.ORDER_FORM_PRICE.name).input_value()
        qty_text = self._page.get_by_role(locators.ORDER_FORM_QUANTITY.role, name=locators.ORDER_FORM_QUANTITY.name).input_value()
        return ObservedOrderForm(
            symbol=(symbol_text or None), side=(side_text.strip().upper() if side_text else None),
            order_type=(type_text.strip().lower() if type_text else None),
            price=_parse_money(price_text), quantity=_parse_money(qty_text),
            observed_at=datetime.now(timezone.utc),
        )

    def observe_confirmation_state(self) -> ObservedConfirmationState:
        dialog = self._page.get_by_role(locators.CONFIRMATION_DIALOG.role, name=locators.CONFIRMATION_DIALOG.name)
        visible = dialog.is_visible()
        message = dialog.inner_text() if visible else None
        return ObservedConfirmationState(visible=visible, message=message, observed_at=datetime.now(timezone.utc))
