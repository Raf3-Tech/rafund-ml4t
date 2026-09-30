"""Structural boundary tests for execution/mobile/ — Phase 4 (read-only
observation, plus one explicitly-authorized navigation exception) must
never gain a broader device-interaction capability by accident.

Mirrors tests/test_execution_browser_boundaries.py's and
tests/test_senior_trader_boundaries.py's approach: derived from the actual
source, not a docstring promise.

tap_bottom_nav() is a deliberate, narrow exception (the account holder
explicitly authorized navigation, not just observation, in-session — see
adb_client.py's module docstring) to an otherwise read-only package. These
tests don't forbid it; they pin down exactly how narrow it is: the only
function that taps at all, restricted to five named, pre-validated
destinations, never raw coordinates from a caller.
"""
from __future__ import annotations

import ast
from pathlib import Path

from execution.mobile.adb_client import _BOTTOM_NAV_TARGETS, tap_bottom_nav

_EXECUTION_MOBILE_DIR = Path(__file__).resolve().parent.parent / "execution" / "mobile"
_ADB_CLIENT_FILE = _EXECUTION_MOBILE_DIR / "adb_client.py"

_INTERACTION_TOKENS = ("input", "am", "pm")
_ALLOWED_INTERACTION_FUNCTION = "tap_bottom_nav"


def _function_defs(tree: ast.Module):
    return [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]


def test_only_tap_bottom_nav_contains_interaction_tokens():
    source = _ADB_CLIENT_FILE.read_text()
    tree = ast.parse(source)

    offenders = []
    for func in _function_defs(tree):
        if func.name == _ALLOWED_INTERACTION_FUNCTION:
            continue
        segment = ast.get_source_segment(source, func) or ""
        for token in _INTERACTION_TOKENS:
            if f'"{token}"' in segment or f"'{token}'" in segment:
                offenders.append((func.name, token))

    assert offenders == [], f"Only {_ALLOWED_INTERACTION_FUNCTION} may issue device-mutating commands: {offenders}"


def test_tap_bottom_nav_accepts_only_a_named_tab_not_raw_coordinates():
    tree = ast.parse(_ADB_CLIENT_FILE.read_text())
    func = next(f for f in _function_defs(tree) if f.name == _ALLOWED_INTERACTION_FUNCTION)
    param_names = [a.arg for a in func.args.args]
    assert param_names == ["tab", "serial"], (
        f"tap_bottom_nav must take exactly (tab, serial) — no raw x/y coordinate "
        f"parameters, so a caller can never tap an arbitrary point: got {param_names}"
    )


def test_bottom_nav_targets_are_exactly_the_five_known_tabs():
    assert set(_BOTTOM_NAV_TARGETS) == {"Prop", "Markets", "Trade", "Portfolio", "Account"}


def test_tap_bottom_nav_rejects_unknown_tab_before_touching_the_device():
    import pytest

    with pytest.raises(ValueError):
        tap_bottom_nav("Buy")  # not a real tab; must never reach a subprocess call


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
