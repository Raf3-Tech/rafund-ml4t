"""Tests for risk/cost_model.py.

The acceptance case is given verbatim in the Kraken Prop task brief: on a
$10,000 account with a $600 MDD buffer, a single $50,000 notional round trip
must cost $40 (6.7% of the buffer) and one day of funding must add $16.50
(2.75%). If these numbers don't come out exactly right, the model is wrong.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from risk.cost_model import funding_cost, round_trip_commission, total_expected_cost

MDD_BUFFER = Decimal("600")


def test_round_trip_commission_matches_prop_spec_acceptance_case():
    commission = round_trip_commission(Decimal("50000"))
    assert commission == Decimal("40.00")
    pct_of_buffer = (commission / MDD_BUFFER * 100).quantize(Decimal("0.1"))
    assert pct_of_buffer == Decimal("6.7")


def test_funding_cost_one_day_matches_prop_spec_acceptance_case():
    funding = funding_cost(Decimal("50000"), Decimal("24"))
    assert funding == Decimal("16.50")
    pct_of_buffer = (funding / MDD_BUFFER * 100).quantize(Decimal("0.01"))
    assert pct_of_buffer == Decimal("2.75")


def test_funding_cost_charges_whole_4h_blocks_not_prorated():
    notional = Decimal("50000")
    # 5 hours held spills into a 2nd block, same charge as a full 8 hours.
    assert funding_cost(notional, Decimal("5")) == funding_cost(notional, Decimal("8"))
    assert funding_cost(notional, Decimal("5")) != funding_cost(notional, Decimal("4"))


def test_funding_cost_zero_hours_is_zero():
    assert funding_cost(Decimal("50000"), Decimal("0")) == Decimal("0")


def test_funding_cost_rejects_negative_hours():
    with pytest.raises(ValueError):
        funding_cost(Decimal("50000"), Decimal("-1"))


def test_total_expected_cost_sums_commission_and_funding():
    setup = SimpleNamespace(notional=Decimal("50000"), expected_hold_hours=Decimal("24"))
    assert total_expected_cost(setup) == Decimal("56.50")


def test_no_floats_leak_into_cost_path():
    # Decimal arithmetic against a float raises TypeError rather than
    # silently truncating precision — the enforcement mechanism for "no
    # floats in the risk/cost path" is Python's own Decimal type, not a
    # runtime check in this module.
    with pytest.raises(TypeError):
        round_trip_commission(50000.0)  # type: ignore[arg-type]
