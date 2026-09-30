"""Tests for execution/mobile/parser.py against a synthetic, committed
fixture (tests/fixtures/kraken_app_prop_screen_fixture.xml) — structurally
identical to a real captured Kraken Pro app dump (see that module's
docstring) but with fabricated numbers, not the user's real account data.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from execution.mobile.parser import parse_prop_account_screen

_FIXTURE = Path(__file__).parent / "fixtures" / "kraken_app_prop_screen_fixture.xml"


def _parse_fixture():
    return parse_prop_account_screen(_FIXTURE.read_text())


def test_account_label_parsed():
    screen = _parse_fixture()
    assert screen.account_label == "Prop • Test Fixture Account"


def test_account_stats_parsed():
    screen = _parse_fixture()
    assert screen.total_equity == Decimal("1234.56")
    assert screen.total_equity_change == "+9.99 USD (+0.81%)"
    assert screen.target_equity == Decimal("1300.00")
    assert screen.daily_loss_limit == Decimal("1197.50")
    assert screen.drawdown_limit == Decimal("1197.50")


def test_favorites_parsed_in_order():
    screen = _parse_fixture()
    assert len(screen.favorites) == 2
    assert screen.favorites[0].symbol == "FOO"
    assert screen.favorites[0].leverage == "5x"
    assert screen.favorites[0].price == Decimal("12.34")
    assert screen.favorites[0].name == "Foo Coin"
    assert screen.favorites[0].change_pct == Decimal("-1.23")
    assert screen.favorites[1].symbol == "BAR"
    assert screen.favorites[1].change_pct == Decimal("3.45")


def test_positions_panel_empty_state_parsed():
    screen = _parse_fixture()
    assert screen.positions_panel.active_tab == "Positions"
    assert screen.positions_panel.empty_message == "No position entries available"
    assert screen.positions_panel.positions == []
    assert screen.positions_panel.orders == []


def test_missing_account_label_returns_empty_string():
    xml_without_label = (
        "<?xml version='1.0'?><hierarchy>"
        "<node class=\"android.widget.TextView\" text=\"Total equity\"/>"
        "<node class=\"android.widget.TextView\" text=\"1.00 USD\"/>"
        "</hierarchy>"
    )
    screen = parse_prop_account_screen(xml_without_label)
    assert screen.account_label == ""
    assert screen.total_equity == Decimal("1.00")


def test_missing_stats_are_none_not_zero():
    xml_minimal = "<?xml version='1.0'?><hierarchy></hierarchy>"
    screen = parse_prop_account_screen(xml_minimal)
    assert screen.total_equity is None
    assert screen.target_equity is None
    assert screen.daily_loss_limit is None
    assert screen.drawdown_limit is None
    assert screen.favorites == []
