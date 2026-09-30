"""Tests for execution/mobile/parser.py::parse_trade_form.

The fixture (tests/fixtures/kraken_app_trade_form_fixture.xml) is
structurally derived from a REAL captured dump of the Kraken Pro Android
app's order-entry form (2026-09-30, content-verified via
dump_ui_stable's expect_contains and cross-checked against a screenshot
taken at the same moment) with fabricated numbers — the real captured
file is never committed, matching kraken_app_prop_screen_fixture.xml's
precedent. parse_trade_form was also run directly against the real
captured dump during development and reproduced every real on-screen
value exactly (Limit, 85247.4, 0.001, TP 86000, SL 84500, Buy BTC) — not
re-asserted here since only the fixture is committed.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from execution.mobile.parser import parse_trade_form

_FIXTURE = Path(__file__).parent / "fixtures" / "kraken_app_trade_form_fixture.xml"


def _parse():
    return parse_trade_form(_FIXTURE.read_text())


def test_parses_order_type():
    assert _parse().order_type == "Limit"


def test_parses_price_and_quantity():
    form = _parse()
    assert form.price == Decimal("12345.6")
    assert form.quantity == Decimal("0.005")


def test_parses_total_and_available():
    form = _parse()
    assert form.total_usd == Decimal("61.73")
    assert form.available_usd == Decimal("5000.00")


def test_parses_tp_sl_enabled_and_values():
    form = _parse()
    assert form.tp_sl_enabled is True
    assert form.take_profit_price == Decimal("13000")
    assert form.stop_loss_price == Decimal("11000")


def test_parses_submit_button_label():
    assert _parse().submit_button_label == "Buy BTC"


def test_side_is_none_documented_gap():
    """No reliable signal for which of Buy/Sell is active was found in
    the real captured dump — see parse_trade_form's docstring."""
    assert _parse().side is None


def test_tp_sl_disabled_when_checkbox_unchecked():
    xml = _FIXTURE.read_text().replace('checked="true"', 'checked="false"')
    form = parse_trade_form(xml)
    assert form.tp_sl_enabled is False
