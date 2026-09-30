"""Phase 5 — drives one ExecutionIntent through the full order-form
population sequence, and verifies what actually landed on screen against
what was intended.

populate_order_form() issues no adb/subprocess calls of its own — it is
composed entirely of calls to the already-reviewed, individually
boundary-tested named functions in adb_client.py (tap_trade_action,
tap_order_type_dropdown/tap_order_type_option, set_order_form_field,
tap_tp_sl_toggle). It adds no new tap target and no new device-mutating
capability; see tests/test_execution_mobile_boundaries.py for the
enumerated set this still matches unchanged, and
test_execution_mobile_order_population_boundaries.py for the check
specific to this module (no subprocess import, no raw adb call).

Per the account holder's explicit, standing risk-mitigation mandate
(2026-09-30): every order this pipeline populates MUST have TP/SL
attached. populate_order_form() calls tap_tp_sl_toggle() and populates
both take_profit/stop_loss fields unconditionally — there is no
entry-only code path.

verify_order_form() is a pure comparison (no device access) between an
ObservedTradeForm (parser.py's parse_trade_form, itself only reachable
via dump_ui_stable — never a single-shot dump) and the ExecutionIntent
that was meant to produce it. This is the checkpoint AGENT_ORCHESTRATION.md
Section 14 calls for before anything is allowed near submission — nothing
in this module, or anywhere else in execution/mobile/, can submit an
order. That is a distinct, not-yet-built capability with its own gate.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional

from execution.mobile import adb_client
from execution.mobile.models import ObservedTradeForm
from senior_trader.models import ExecutionIntent


def _field_value(value: Decimal) -> str:
    """Fixed-point string for set_order_form_field — never scientific
    notation (which its [0-9.]+ regex would reject outright)."""
    return format(value, "f")


def populate_order_form(intent: ExecutionIntent, serial: Optional[str] = None) -> None:
    """Opens the order form for intent.side and populates it end to end:
    Limit order type, quantity, price, and — unconditionally — TP/SL.
    Does not verify the result (call verify_order_form after re-observing
    the screen) and does not submit."""
    if intent.order_type != "limit":
        raise ValueError(f"populate_order_form only supports limit orders, got {intent.order_type!r}")
    if intent.side not in ("BUY", "SELL"):
        raise ValueError(f"populate_order_form only supports BUY/SELL, got {intent.side!r}")

    action = "Buy" if intent.side == "BUY" else "Sell"
    adb_client.tap_trade_action(action, serial)
    adb_client.tap_order_type_dropdown(serial)
    adb_client.tap_order_type_option("Limit", serial)
    adb_client.set_order_form_field("limit_price", _field_value(intent.price), serial)
    adb_client.set_order_form_field("quantity", _field_value(intent.quantity), serial)
    # Risk-mitigation mandate: unconditional, never skipped for a bare
    # entry-only order.
    adb_client.tap_tp_sl_toggle(serial)
    adb_client.set_order_form_field("take_profit", _field_value(intent.take_profit_price), serial)
    adb_client.set_order_form_field("stop_loss", _field_value(intent.stop_price), serial)


@dataclass(frozen=True)
class OrderFormVerification:
    matches: bool
    mismatches: List[str]


def verify_order_form(observed: ObservedTradeForm, intent: ExecutionIntent) -> OrderFormVerification:
    """Pure comparison, no device access. Every AGENT_ORCHESTRATION.md
    Section 14 field this observer can actually see is checked; fields it
    cannot see (account/market identity, no-stale-signal, no-conflicting-
    order, risk-state) are out of scope for this function — see
    state/integration_plan.md's Phase 5 section for what's still missing
    before a real submission gate exists."""
    mismatches: List[str] = []

    if observed.order_type != "Limit":
        mismatches.append(f"order_type: expected 'Limit', observed {observed.order_type!r}")
    if observed.price != intent.price:
        mismatches.append(f"price: expected {intent.price}, observed {observed.price}")
    if observed.quantity != intent.quantity:
        mismatches.append(f"quantity: expected {intent.quantity}, observed {observed.quantity}")
    if not observed.tp_sl_enabled:
        mismatches.append("tp_sl_enabled: expected True (risk-mitigation mandate), observed False")
    if observed.take_profit_price != intent.take_profit_price:
        mismatches.append(
            f"take_profit_price: expected {intent.take_profit_price}, observed {observed.take_profit_price}"
        )
    if observed.stop_loss_price != intent.stop_price:
        mismatches.append(f"stop_loss_price: expected {intent.stop_price}, observed {observed.stop_loss_price}")

    expected_side = "Buy" if intent.side == "BUY" else "Sell"
    expected_base_asset = intent.symbol.split("/")[0]
    expected_submit_label = f"{expected_side} {expected_base_asset}"
    if observed.submit_button_label != expected_submit_label:
        mismatches.append(
            f"submit_button_label: expected {expected_submit_label!r}, observed {observed.submit_button_label!r}"
        )

    return OrderFormVerification(matches=not mismatches, mismatches=mismatches)
