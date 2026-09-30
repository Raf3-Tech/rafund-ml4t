"""Structural boundary tests for execution/mobile/ — Phase 4 (read-only
observation) must never gain a device-interaction capability by accident.

Mirrors tests/test_execution_browser_boundaries.py's and
tests/test_senior_trader_boundaries.py's approach: derived from the actual
source, not a docstring promise.
"""
from __future__ import annotations

import re
from pathlib import Path

_EXECUTION_MOBILE_DIR = Path(__file__).resolve().parent.parent / "execution" / "mobile"
_ADB_CLIENT_FILE = _EXECUTION_MOBILE_DIR / "adb_client.py"

# Only checked against adb_client.py — the one module that builds `adb`
# argv lists at all. Deliberately just the subcommand tokens themselves
# (as bare quoted string literals, i.e. code, not prose): "input" is
# `adb shell input <tap|text|swipe|keyevent>`'s own subcommand name, so
# catching "input" alone covers all of those; "am"/"pm" start or kill
# apps. Not checked repo-wide because a word this short ("am", "pm") would
# false-positive constantly outside a narrow, known-safe file.
_FORBIDDEN_ADB_TOKENS = ("input", "am", "pm")


def test_no_forbidden_adb_subcommands_in_adb_client():
    source = _ADB_CLIENT_FILE.read_text()
    offenders = [
        token for token in _FORBIDDEN_ADB_TOKENS
        if re.search(rf"[\"']{re.escape(token)}[\"']", source)
    ]
    assert offenders == [], f"execution/mobile/adb_client.py must not issue device-mutating adb commands: {offenders}"


def test_no_ccxt_or_playwright_import_anywhere_in_execution_mobile():
    """This package automates ADB/uiautomator, never an exchange API or a
    browser — neither has any business here."""
    import ast

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
