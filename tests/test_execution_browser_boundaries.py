"""Structural boundary tests for execution/browser/ — Phase 4 (read-only
observation) must never gain a submission capability by accident.

Mirrors tests/test_senior_trader_boundaries.py's approach: a structural
guarantee derived from the actual source, not a docstring promise.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from execution.browser.kraken_terminal import KrakenTerminalObserver

_EXECUTION_BROWSER_DIR = Path(__file__).resolve().parent.parent / "execution" / "browser"

_FORBIDDEN_METHOD_NAME_FRAGMENTS = ("submit", "place_order", "cancel", "modify", "close_position")


def test_kraken_terminal_observer_has_no_submission_capable_method():
    method_names = [name for name, _ in inspect.getmembers(KrakenTerminalObserver, predicate=inspect.isfunction)]
    offenders = [
        name for name in method_names
        if any(fragment in name.lower() for fragment in _FORBIDDEN_METHOD_NAME_FRAGMENTS)
    ]
    assert offenders == [], f"KrakenTerminalObserver must not have order-submission-capable methods: {offenders}"


def test_kraken_terminal_observer_only_has_observe_methods_and_dunders():
    method_names = [name for name, _ in inspect.getmembers(KrakenTerminalObserver, predicate=inspect.isfunction)]
    non_observe = [
        name for name in method_names
        if not name.startswith("observe_") and not (name.startswith("__") and name.endswith("__"))
    ]
    assert non_observe == [], f"Every public method on KrakenTerminalObserver must be observe_* : found {non_observe}"


def _imported_module_names(source: str) -> set:
    tree = ast.parse(source)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_no_ccxt_import_anywhere_in_execution_browser():
    """This package automates a UI, never an exchange API — ccxt has no
    business here, in Phase 4 or any later phase of it."""
    offenders = []
    for path in _EXECUTION_BROWSER_DIR.rglob("*.py"):
        imported = _imported_module_names(path.read_text())
        if any(name == "ccxt" or name.startswith("ccxt.") for name in imported):
            offenders.append(str(path))
    assert offenders == [], f"execution/browser/ must not import ccxt: {offenders}"


def test_playwright_is_never_imported_at_module_top_level():
    """playwright must only ever be imported inside a function body, or
    inside an `if TYPE_CHECKING:` block (which never executes at runtime),
    so execution/browser/ stays importable without it installed. Only
    walking `tree.body` (direct top-level statements) naturally excludes
    both function-local imports and TYPE_CHECKING-guarded ones — neither
    sits directly in the module body."""
    offenders = []
    for path in _EXECUTION_BROWSER_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in tree.body:
            if isinstance(node, ast.Import) and any(a.name.startswith("playwright") for a in node.names):
                offenders.append(str(path))
            elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("playwright"):
                offenders.append(str(path))
    assert offenders == [], f"playwright must be a function-local or TYPE_CHECKING-only import: {offenders}"
