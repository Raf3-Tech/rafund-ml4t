"""Tests for execution/browser/kraken_terminal.py's parsing/mapping logic.

Uses lightweight fake Page/Locator test doubles rather than a real
Playwright browser or playwright's own mock fixtures — this session has no
access to a real (or recorded) Kraken terminal to test against, and
playwright itself isn't installed in this environment (see requirements.txt's
Phase 4 section). These fakes exercise exactly the same call shape
kraken_terminal.py makes (get_by_role(role, name=...).inner_text(), table
row iteration, input_value(), is_visible()) so the tests genuinely validate
the parsing/mapping logic — they do not, and cannot, validate that
execution/browser/locators.py's role/name pairs match the real Kraken DOM
(that requires a human with real terminal access; see that module's
docstring).
"""
from __future__ import annotations

from decimal import Decimal

from execution.browser import locators
from execution.browser.kraken_terminal import KrakenTerminalObserver, _parse_money


class _FakeLocator:
    def __init__(self, *, text=None, input_value=None, visible=True, rows=None):
        self._text = text
        self._input_value = input_value
        self._visible = visible
        self._rows = rows or []

    def inner_text(self):
        return self._text

    def input_value(self):
        return self._input_value

    def is_visible(self):
        return self._visible

    def locator(self, selector):
        if selector == "tbody tr":
            return _RowsLocator(self._rows)
        return self


class _RowsLocator:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return [_RowLocator(cells) for cells in self._rows]


class _RowLocator:
    def __init__(self, cells):
        self._cells = cells

    def locator(self, selector):
        return self

    def all_inner_texts(self):
        return self._cells


class _FakePage:
    def __init__(self, by_role: dict):
        self._by_role = by_role

    def get_by_role(self, role, name=None):
        return self._by_role[(role, name)]


def _key(role_locator) -> tuple:
    return (role_locator.role, role_locator.name)


# ── _parse_money ─────────────────────────────────────────────────────────

def test_parse_money_strips_currency_and_commas():
    assert _parse_money("$12,345.67") == Decimal("12345.67")


def test_parse_money_handles_negative():
    assert _parse_money("-42.50") == Decimal("-42.50")


def test_parse_money_returns_none_for_placeholder_text():
    assert _parse_money("--") is None
    assert _parse_money("") is None
    assert _parse_money(None) is None


def test_parse_money_returns_none_for_unparseable_text():
    assert _parse_money("not a number") is None


# ── observe_account_state ────────────────────────────────────────────────

def test_observe_account_state_parses_prop_account():
    page = _FakePage({
        _key(locators.ACCOUNT_SELECTOR): _FakeLocator(text="Prop Eval $5K"),
        _key(locators.BALANCE): _FakeLocator(text="$5,123.45"),
        _key(locators.EQUITY): _FakeLocator(text="$5,098.00"),
        _key(locators.DAILY_LOSS_LIMIT): _FakeLocator(text="$150.00"),
        _key(locators.DRAWDOWN_LIMIT): _FakeLocator(text="$300.00"),
    })
    state = KrakenTerminalObserver(page).observe_account_state()

    assert state.account_label == "Prop Eval $5K"
    assert state.is_prop_account is True
    assert state.balance == Decimal("5123.45")
    assert state.equity == Decimal("5098.00")
    assert state.daily_loss_limit == Decimal("150.00")
    assert state.drawdown_limit == Decimal("300.00")


def test_observe_account_state_non_prop_account_flagged_false():
    page = _FakePage({
        _key(locators.ACCOUNT_SELECTOR): _FakeLocator(text="Spot Wallet"),
        _key(locators.BALANCE): _FakeLocator(text="$100.00"),
        _key(locators.EQUITY): _FakeLocator(text="$100.00"),
        _key(locators.DAILY_LOSS_LIMIT): _FakeLocator(text="--"),
        _key(locators.DRAWDOWN_LIMIT): _FakeLocator(text="--"),
    })
    state = KrakenTerminalObserver(page).observe_account_state()

    assert state.is_prop_account is False
    assert state.daily_loss_limit is None
    assert state.drawdown_limit is None


# ── observe_positions ────────────────────────────────────────────────────

def test_observe_positions_maps_table_rows():
    page = _FakePage({
        _key(locators.POSITIONS_TABLE): _FakeLocator(rows=[
            ["BTC/USD", "long", "0.5", "60000", "125.50"],
            ["ETH/USD", "short", "2.0", "3000", "-40.00"],
        ]),
    })
    positions = KrakenTerminalObserver(page).observe_positions()

    assert len(positions) == 2
    assert positions[0].symbol == "BTC/USD"
    assert positions[0].side == "LONG"
    assert positions[0].quantity == Decimal("0.5")
    assert positions[0].entry_price == Decimal("60000")
    assert positions[0].unrealized_pnl == Decimal("125.50")
    assert positions[1].side == "SHORT"
    assert positions[1].unrealized_pnl == Decimal("-40.00")


def test_observe_positions_skips_malformed_rows():
    page = _FakePage({
        _key(locators.POSITIONS_TABLE): _FakeLocator(rows=[["BTC/USD", "long"]]),  # too few cells
    })
    positions = KrakenTerminalObserver(page).observe_positions()
    assert positions == []


def test_observe_positions_empty_table_returns_empty_list():
    page = _FakePage({_key(locators.POSITIONS_TABLE): _FakeLocator(rows=[])})
    assert KrakenTerminalObserver(page).observe_positions() == []


# ── observe_open_orders ──────────────────────────────────────────────────

def test_observe_open_orders_maps_table_rows():
    page = _FakePage({
        _key(locators.ORDERS_TABLE): _FakeLocator(rows=[
            ["ord-1", "BTC/USD", "buy", "limit", "59000", "0.1", "open"],
        ]),
    })
    orders = KrakenTerminalObserver(page).observe_open_orders()

    assert len(orders) == 1
    assert orders[0].order_id == "ord-1"
    assert orders[0].side == "BUY"
    assert orders[0].order_type == "limit"
    assert orders[0].price == Decimal("59000")
    assert orders[0].status == "open"


# ── observe_market ───────────────────────────────────────────────────────

def test_observe_market_parses_top_of_book():
    page = _FakePage({
        _key(locators.BEST_BID): _FakeLocator(text="59900.00"),
        _key(locators.BEST_ASK): _FakeLocator(text="59901.00"),
        _key(locators.LAST_PRICE): _FakeLocator(text="59900.50"),
    })
    market = KrakenTerminalObserver(page).observe_market("BTC/USD")

    assert market.symbol == "BTC/USD"
    assert market.best_bid == Decimal("59900.00")
    assert market.best_ask == Decimal("59901.00")
    assert market.last_price == Decimal("59900.50")


# ── observe_order_form ───────────────────────────────────────────────────

def test_observe_order_form_reads_current_form_state():
    page = _FakePage({
        _key(locators.ORDER_FORM_SYMBOL): _FakeLocator(input_value="BTC/USD"),
        _key(locators.ORDER_FORM_SIDE): _FakeLocator(text="Buy"),
        _key(locators.ORDER_FORM_TYPE): _FakeLocator(input_value="Limit"),
        _key(locators.ORDER_FORM_PRICE): _FakeLocator(input_value="60000"),
        _key(locators.ORDER_FORM_QUANTITY): _FakeLocator(input_value="0.25"),
    })
    form = KrakenTerminalObserver(page).observe_order_form()

    assert form.symbol == "BTC/USD"
    assert form.side == "BUY"
    assert form.order_type == "limit"
    assert form.price == Decimal("60000")
    assert form.quantity == Decimal("0.25")


def test_observe_order_form_empty_fields_are_none_not_empty_string():
    page = _FakePage({
        _key(locators.ORDER_FORM_SYMBOL): _FakeLocator(input_value=""),
        _key(locators.ORDER_FORM_SIDE): _FakeLocator(text=""),
        _key(locators.ORDER_FORM_TYPE): _FakeLocator(input_value=""),
        _key(locators.ORDER_FORM_PRICE): _FakeLocator(input_value=""),
        _key(locators.ORDER_FORM_QUANTITY): _FakeLocator(input_value=""),
    })
    form = KrakenTerminalObserver(page).observe_order_form()

    assert form.symbol is None
    assert form.side is None
    assert form.order_type is None


# ── observe_confirmation_state ───────────────────────────────────────────

def test_observe_confirmation_state_visible_with_message():
    page = _FakePage({
        _key(locators.CONFIRMATION_DIALOG): _FakeLocator(text="Order placed: BTC/USD buy 0.1 @ 60000", visible=True),
    })
    result = KrakenTerminalObserver(page).observe_confirmation_state()
    assert result.visible is True
    assert result.message == "Order placed: BTC/USD buy 0.1 @ 60000"


def test_observe_confirmation_state_not_visible_has_no_message():
    page = _FakePage({_key(locators.CONFIRMATION_DIALOG): _FakeLocator(visible=False)})
    result = KrakenTerminalObserver(page).observe_confirmation_state()
    assert result.visible is False
    assert result.message is None
