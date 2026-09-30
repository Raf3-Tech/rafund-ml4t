"""Structural boundary tests for execution/mobile/order_population.py —
this module must remain a pure orchestrator of adb_client.py's already
boundary-tested named functions, never a second place that talks to
subprocess/adb directly, and never a place that gains a submission
capability."""
from __future__ import annotations

import ast
from pathlib import Path

_FILE = Path(__file__).resolve().parent.parent / "execution" / "mobile" / "order_population.py"


def test_no_subprocess_import():
    tree = ast.parse(_FILE.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    assert "subprocess" not in names


def test_no_raw_adb_shell_invocation():
    source = _FILE.read_text()
    assert '"shell"' not in source and "'shell'" not in source


def test_no_submit_or_place_or_confirm_function_defined():
    tree = ast.parse(_FILE.read_text())
    forbidden_substrings = ("submit", "place_order", "confirm")
    offenders = [
        node.name for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and any(s in node.name.lower() for s in forbidden_substrings)
    ]
    assert offenders == [], f"order_population.py must not define a submission-capable function: {offenders}"


def test_populate_order_form_only_calls_adb_client_dot_prefixed_functions():
    """Every device-mutating call inside populate_order_form must go
    through the `adb_client.` module reference (the already-gated, named
    functions) — never a bare/local call that could bypass that layer."""
    tree = ast.parse(_FILE.read_text())
    func = next(f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef) and f.name == "populate_order_form")
    for node in ast.walk(func):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name):
                assert node.func.value.id == "adb_client", (
                    f"Unexpected module-qualified call target in populate_order_form: {node.func.value.id}"
                )
