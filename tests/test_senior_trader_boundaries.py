"""Structural boundary test for Phase 1: senior_trader/ must have no
execution capability at all. Mirrors
tests/test_publish_routes.py::test_registered_publish_routes_are_get_only —
a structural guarantee, not just a convention documented in a docstring.

AGENT_ORCHESTRATION.md Section 48 ("do NOT immediately implement autonomous
live trading") and state/integration_plan.md Section 6 both say Phase 1 must
not import anything that can place, touch, or automate an order. This test
enforces that by scanning the actual source, not by trusting a comment.
"""
from __future__ import annotations

import ast
from pathlib import Path

_SENIOR_TRADER_DIR = Path(__file__).resolve().parent.parent / "senior_trader"

_FORBIDDEN_MODULES = {
    "ccxt",
    "playwright",
    "trading.execution",
    "trading.live_trader",
}


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


def test_no_execution_capable_imports_anywhere_in_senior_trader():
    offenders = []
    for path in _SENIOR_TRADER_DIR.rglob("*.py"):
        source = path.read_text()
        imported = _imported_module_names(source)
        for forbidden in _FORBIDDEN_MODULES:
            if any(name == forbidden or name.startswith(forbidden + ".") for name in imported):
                offenders.append((str(path), forbidden))

    assert offenders == [], f"senior_trader/ must not import execution-capable modules: {offenders}"
