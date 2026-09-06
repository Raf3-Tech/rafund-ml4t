"""Kraken Prop pre-trade gate — the only path to a live setup.

`evaluate()` is the single function allowed to turn a PendingSignal into
something a human is actually shown as ready to place. Every call persists
its full inputs to `gate_decisions` (see migration 0019) so the gate is
auditable after the fact, per the brief. Nothing else in this codebase may
write a PendingSignal to 'active' status without having gone through this
first — see signals/pending_signal_detector.py::scan_kraken_signals, the
only call site, and tests/test_pretrade_gate.py's
test_rejected_setup_is_never_persisted_as_active for the behavioral proof.

Two of the six reject rules below reference numbers the brief does not
supply (correlation cap, consecutive-loss limit) — those are config-
overridable placeholders, explicitly flagged, not invented spec. Do not
treat their defaults as calibrated; a risk owner needs to set them for real
before this gate runs against a live account.

Leverage caps (BTC 10x/$1M, NDX 10x/$1M, S&P 10x/$2M, SOL 5x/$500K, HYPE
3x/$200K — verified against blog.kraken.com, August 2026, subject to change)
are NOT one of the six reject rules the brief specifies for this gate; they
are recorded here for a future rule/UI use, not enforced by evaluate().
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, NamedTuple, Optional

from risk.prop_account import PropAccountState

FEE_FILTER_MAX_PCT = Decimal("0.15")  # brief: expected_cost > 15% of expected R -> reject

# Placeholders — NOT given by the brief. A risk owner must calibrate these
# before this gate protects a live account; treat as unset, not authoritative.
DEFAULT_CORRELATION_CAP_PCT = Decimal("1.0")  # net directional notional vs. equity
DEFAULT_CONSECUTIVE_LOSS_LIMIT = 3

# Verified current values (blog.kraken.com, 2026-08) — informational only,
# not enforced by evaluate() (see module docstring).
KRAKEN_LEVERAGE_CAPS = {
    "BTC": {"max_leverage": Decimal("10"), "notional_cap_usd": Decimal("1000000")},
    "NDX": {"max_leverage": Decimal("10"), "notional_cap_usd": Decimal("1000000")},
    "SPX": {"max_leverage": Decimal("10"), "notional_cap_usd": Decimal("2000000")},
    "SOL": {"max_leverage": Decimal("5"), "notional_cap_usd": Decimal("500000")},
    "HYPE": {"max_leverage": Decimal("3"), "notional_cap_usd": Decimal("200000")},
}

APPROVE = "APPROVE"
REDUCE = "REDUCE"
REJECT = "REJECT"


class OpenPosition(NamedTuple):
    symbol: str
    side: str  # LONG | SHORT
    notional: Decimal


@dataclass
class GateContext:
    """Everything evaluate() needs beyond the setup and account state.
    Fields without live data sources yet (consecutive-loss tracking,
    strategy confidence bands from Phase 6) are plain inputs the caller must
    supply — this gate does not compute them."""
    open_positions: List[OpenPosition] = field(default_factory=list)
    consecutive_losses_today: int = 0
    consecutive_loss_limit: int = DEFAULT_CONSECUTIVE_LOSS_LIMIT
    correlation_cap_pct: Decimal = DEFAULT_CORRELATION_CAP_PCT
    strategy_benched: bool = False


@dataclass(frozen=True)
class GateDecision:
    action: str  # APPROVE | REDUCE | REJECT
    reason_code: str
    details: dict


def _effective_daily_room(account: PropAccountState) -> Decimal:
    """Room to OUR hard daily floor (tighter than Kraken's mdl_floor),
    haircut (halved) further once the soft floor is active — the same
    halving response trading.paper_trader's existing soft floor already
    uses for its own (dollar-amount) two-tier system."""
    from risk.prop_account import DAILY_HARD_FLOOR_PCT

    if account.daily_hard_triggered:
        return Decimal("0")
    our_floor = account.daily_start_balance * (1 - DAILY_HARD_FLOOR_PCT)
    room = account.equity - our_floor
    if account.daily_soft_triggered:
        room = room / 2
    return max(room, Decimal("0"))


def _effective_lifetime_room(account: PropAccountState) -> Decimal:
    """Room to OUR lifetime hard floor (70% of Kraken's own tier-dependent
    MDD room). No lifetime soft tier exists (Phase 2 only defines a hard
    one), so there's no haircut step here — just zero once triggered."""
    from risk.prop_account import LIFETIME_HARD_FLOOR_FRACTION

    if account.lifetime_hard_triggered:
        return Decimal("0")
    our_floor = account.peak_balance * (1 - LIFETIME_HARD_FLOOR_FRACTION * account.kraken_mdd_pct)
    return max(account.equity - our_floor, Decimal("0"))


def _net_directional_exposure(open_positions: List[OpenPosition], setup) -> Decimal:
    """Crypto treated as approximately one factor: net directional notional
    (signed by side), not per-symbol size. Five different longs are one
    large long."""
    net = Decimal("0")
    for pos in open_positions:
        net += pos.notional if pos.side == "LONG" else -pos.notional
    net += setup.position_size_usd if setup.direction == "LONG" else -setup.position_size_usd
    return net


def evaluate(setup, account_state: PropAccountState, context: Optional[GateContext] = None) -> GateDecision:
    """The only function allowed to approve a PendingSignal for display as a
    live, ready-to-place setup. `setup` is a signals.pending_signal_detector.
    PendingSignal (not imported here to avoid a circular import — the two
    modules' relationship is documented in both docstrings)."""
    context = context or GateContext()

    if context.strategy_benched:
        return GateDecision(REJECT, "strategy_benched", {"strategy_name": setup.strategy_name})

    if context.consecutive_losses_today >= context.consecutive_loss_limit:
        return GateDecision(
            REJECT, "consecutive_loss_breaker",
            {"consecutive_losses_today": context.consecutive_losses_today,
             "limit": context.consecutive_loss_limit},
        )

    if account_state.daily_hard_triggered:
        return GateDecision(REJECT, "daily_hard_floor", {"daily_loss_pct": str(account_state.daily_loss_pct)})

    if account_state.lifetime_hard_triggered:
        return GateDecision(
            REJECT, "lifetime_hard_floor",
            {"lifetime_drawdown_fraction": str(account_state.lifetime_drawdown_fraction)},
        )

    daily_room = _effective_daily_room(account_state)
    if setup.worst_case_loss > daily_room:
        return GateDecision(
            REJECT, "daily_room_insufficient",
            {"worst_case_loss": str(setup.worst_case_loss), "daily_room_after_haircut": str(daily_room)},
        )

    lifetime_room = _effective_lifetime_room(account_state)
    if setup.worst_case_loss > lifetime_room:
        return GateDecision(
            REJECT, "lifetime_room_insufficient",
            {"worst_case_loss": str(setup.worst_case_loss), "lifetime_room": str(lifetime_room)},
        )

    r_dollars = setup.qty * abs(setup.limit_price - setup.stop_price)
    if r_dollars > 0 and setup.expected_cost > r_dollars * FEE_FILTER_MAX_PCT:
        return GateDecision(
            REJECT, "fee_filter",
            {"expected_cost": str(setup.expected_cost), "r_dollars": str(r_dollars),
             "max_allowed": str(r_dollars * FEE_FILTER_MAX_PCT)},
        )

    net_exposure = _net_directional_exposure(context.open_positions, setup)
    cap = account_state.equity * context.correlation_cap_pct
    if abs(net_exposure) > cap:
        return GateDecision(
            REJECT, "correlation_cap",
            {"net_directional_exposure": str(net_exposure), "cap": str(cap)},
        )

    if account_state.daily_soft_triggered:
        return GateDecision(
            REDUCE, "daily_soft_floor_active",
            {"daily_room_after_haircut": str(daily_room), "worst_case_loss": str(setup.worst_case_loss)},
        )

    return GateDecision(APPROVE, "all_checks_passed", {})


def _json_safe(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def persist_decision(db, setup, account_state: PropAccountState, context: GateContext, decision: GateDecision) -> None:
    """Append-only audit row — every evaluate() call, approved or not, with
    full inputs, so the gate's behavior is reconstructable after the fact."""
    inputs = {
        "setup": {
            "strategy_name": setup.strategy_name, "symbol": setup.symbol, "direction": setup.direction,
            "limit_price": setup.limit_price, "stop_price": setup.stop_price,
            "qty": setup.qty, "position_size_usd": setup.position_size_usd,
            "worst_case_loss": setup.worst_case_loss, "expected_cost": setup.expected_cost,
            "r_multiple": setup.r_multiple, "leverage_required": setup.leverage_required,
        },
        "account_state": {
            "balance": account_state.balance, "equity": account_state.equity,
            "daily_room_remaining": account_state.daily_room_remaining,
            "lifetime_room_remaining": account_state.lifetime_room_remaining,
            "daily_soft_triggered": account_state.daily_soft_triggered,
            "daily_hard_triggered": account_state.daily_hard_triggered,
            "lifetime_hard_triggered": account_state.lifetime_hard_triggered,
        },
        "context": {
            "consecutive_losses_today": context.consecutive_losses_today,
            "consecutive_loss_limit": context.consecutive_loss_limit,
            "correlation_cap_pct": context.correlation_cap_pct,
            "strategy_benched": context.strategy_benched,
            "open_position_count": len(context.open_positions),
        },
        "decision": {"action": decision.action, "reason_code": decision.reason_code, "details": decision.details},
    }
    conn = db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO gate_decisions
                (strategy_name, symbol, direction, action, reason_code, inputs)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                setup.strategy_name, setup.symbol, setup.direction,
                decision.action, decision.reason_code, json.dumps(_json_safe(inputs)),
            ),
        )
        conn.commit()
        cur.close()
    finally:
        db.return_connection(conn)
