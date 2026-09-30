"""Structural boundary tests for execution/mobile/ — Phase 4 (read-only
observation, plus one explicitly-authorized navigation exception) must
never gain a broader device-interaction capability by accident.

Mirrors tests/test_execution_browser_boundaries.py's and
tests/test_senior_trader_boundaries.py's approach: derived from the actual
source, not a docstring promise.

tap_bottom_nav() and tap_trade_action() are deliberate, narrow exceptions
(the account holder explicitly authorized navigation in-session, then
separately confirmed the Kraken ToS/Prop Evaluation Terms/Funded Trader
Agreement review required before Phase 5 work — see adb_client.py's module
docstring) to an otherwise read-only package. These tests don't forbid
them; they pin down exactly how narrow each is: the only functions that
tap at all, each restricted to a small set of named, pre-validated
targets, never raw coordinates from a caller. Note tap_trade_action opens
the order-entry form only — it cannot place an order by itself; that is a
separate, not-yet-built capability with its own gate.
"""
from __future__ import annotations

import ast
from pathlib import Path

from execution.mobile.adb_client import (
    _BOTTOM_NAV_TARGETS,
    _ORDER_TYPE_OPTION_TARGETS,
    _TRADE_ACTION_TARGETS,
    _TRADE_VIEW_TOGGLE_TARGETS,
    tap_bottom_nav,
    tap_order_type_dropdown,
    tap_order_type_option,
    tap_trade_action,
    tap_trade_view_toggle,
)

_EXECUTION_MOBILE_DIR = Path(__file__).resolve().parent.parent / "execution" / "mobile"
_ADB_CLIENT_FILE = _EXECUTION_MOBILE_DIR / "adb_client.py"

_INTERACTION_TOKENS = ("input", "am", "pm")
_ALLOWED_INTERACTION_FUNCTIONS = {
    "tap_bottom_nav", "tap_trade_action", "tap_trade_view_toggle",
    "tap_order_type_dropdown", "tap_order_type_option",
}


def _function_defs(tree: ast.Module):
    return [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]


def test_only_allowed_functions_contain_interaction_tokens():
    source = _ADB_CLIENT_FILE.read_text()
    tree = ast.parse(source)

    offenders = []
    for func in _function_defs(tree):
        if func.name in _ALLOWED_INTERACTION_FUNCTIONS:
            continue
        segment = ast.get_source_segment(source, func) or ""
        for token in _INTERACTION_TOKENS:
            if f'"{token}"' in segment or f"'{token}'" in segment:
                offenders.append((func.name, token))

    assert offenders == [], f"Only {sorted(_ALLOWED_INTERACTION_FUNCTIONS)} may issue device-mutating commands: {offenders}"


def test_tap_bottom_nav_accepts_only_a_named_tab_not_raw_coordinates():
    tree = ast.parse(_ADB_CLIENT_FILE.read_text())
    func = next(f for f in _function_defs(tree) if f.name == "tap_bottom_nav")
    param_names = [a.arg for a in func.args.args]
    assert param_names == ["tab", "serial"], (
        f"tap_bottom_nav must take exactly (tab, serial) — no raw x/y coordinate "
        f"parameters, so a caller can never tap an arbitrary point: got {param_names}"
    )


def test_tap_trade_action_accepts_only_a_named_action_not_raw_coordinates():
    tree = ast.parse(_ADB_CLIENT_FILE.read_text())
    func = next(f for f in _function_defs(tree) if f.name == "tap_trade_action")
    param_names = [a.arg for a in func.args.args]
    assert param_names == ["action", "serial"], (
        f"tap_trade_action must take exactly (action, serial) — no raw x/y coordinate "
        f"parameters: got {param_names}"
    )


def test_bottom_nav_targets_are_exactly_the_five_known_tabs():
    assert set(_BOTTOM_NAV_TARGETS) == {"Prop", "Markets", "Trade", "Portfolio", "Account"}


def test_trade_action_targets_are_exactly_buy_and_sell():
    assert set(_TRADE_ACTION_TARGETS) == {"Buy", "Sell"}


def test_tap_trade_view_toggle_accepts_only_a_named_icon_not_raw_coordinates():
    tree = ast.parse(_ADB_CLIENT_FILE.read_text())
    func = next(f for f in _function_defs(tree) if f.name == "tap_trade_view_toggle")
    param_names = [a.arg for a in func.args.args]
    assert param_names == ["icon", "serial"], (
        f"tap_trade_view_toggle must take exactly (icon, serial) — no raw x/y coordinate "
        f"parameters: got {param_names}"
    )


def test_trade_view_toggle_targets_are_exactly_left_and_right():
    assert set(_TRADE_VIEW_TOGGLE_TARGETS) == {"left", "right"}


def test_tap_trade_view_toggle_rejects_unknown_icon_before_touching_the_device():
    import pytest

    with pytest.raises(ValueError):
        tap_trade_view_toggle("middle")


def test_tap_order_type_dropdown_takes_no_selectable_argument():
    tree = ast.parse(_ADB_CLIENT_FILE.read_text())
    func = next(f for f in _function_defs(tree) if f.name == "tap_order_type_dropdown")
    param_names = [a.arg for a in func.args.args]
    assert param_names == ["serial"], (
        f"tap_order_type_dropdown always opens the same dropdown — it must take only "
        f"(serial), no coordinate or option argument: got {param_names}"
    )


def test_tap_order_type_option_accepts_only_a_named_option_not_raw_coordinates():
    tree = ast.parse(_ADB_CLIENT_FILE.read_text())
    func = next(f for f in _function_defs(tree) if f.name == "tap_order_type_option")
    param_names = [a.arg for a in func.args.args]
    assert param_names == ["option", "serial"], (
        f"tap_order_type_option must take exactly (option, serial) — no raw x/y "
        f"coordinate parameters: got {param_names}"
    )


def test_order_type_option_targets_are_exactly_limit_market_trigger():
    assert set(_ORDER_TYPE_OPTION_TARGETS) == {"Limit", "Market", "Trigger"}


def test_tap_order_type_option_rejects_unknown_option_before_touching_the_device():
    import pytest

    with pytest.raises(ValueError):
        tap_order_type_option("StopLimit")


def test_tap_bottom_nav_rejects_unknown_tab_before_touching_the_device():
    import pytest

    with pytest.raises(ValueError):
        tap_bottom_nav("Buy")  # not a real tab; must never reach a subprocess call


def test_tap_trade_action_rejects_unknown_action_before_touching_the_device():
    import pytest

    with pytest.raises(ValueError):
        tap_trade_action("Prop")  # not a trade action; must never reach a subprocess call


def test_no_ccxt_or_playwright_import_anywhere_in_execution_mobile():
    """This package automates ADB/uiautomator, never an exchange API or a
    browser — neither has any business here."""
    offenders = []
    for path in _EXECUTION_MOBILE_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
        for forbidden in ("ccxt", "playwright"):
            if any(n == forbidden or n.startswith(forbidden + ".") for n in names):
                offenders.append((str(path), forbidden))
    assert offenders == [], f"execution/mobile/ must not import ccxt or playwright: {offenders}"
