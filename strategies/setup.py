"""Risk-based sizing math for a Kraken Prop trade setup.

Pure Decimal functions consumed by signals.pending_signal_detector's
_build_payload — deliberately NOT a second dataclass alongside PendingSignal.
Both cover the same concern (a Kraken-only, manual-execution setup for a
human to place by hand); per AGENTS.md Rule 1, PendingSignal stays the one
dataclass and this module supplies the formulas that populate its new
risk/cost fields. See AGENTS.md's KRAKEN PROP GAP BACKLOG for the full
consolidation rationale.

BaseStrategy.generate_signals is deliberately unchanged — still a per-bar
pd.Series of BUY/SELL/HOLD, the vectorized backtest/leaderboard core for all
13 strategies. These functions consume one already-resolved live signal
(price, stop, target), the same "live signal -> proposal" boundary
signals/pending_signal_detector.py already draws; nothing here touches the
backtest path.

Sizing is risk-based, never allocation-based: derive_size() is the ONLY
function that determines position size, and it is never a function of
leverage (leverage only changes margin efficiency for a given size — see
derive_leverage_required, and the leverage-invariance test in
tests/test_strategy_setup.py). This deliberately does not reuse
trading.paper_trader._target_notional, whose leg-allocation + headroom-cap
sizing serves a different concern: a diversified book of many
simultaneously-open *automatic* paper positions across every exchange, not
one manually-placed Kraken Prop setup sized purely off its own stop
distance. That's also why the risk_pct default below (0.25%) is its own
constant rather than reading config.loader's shared risk_per_trade_pct
(0.5%) — that value is tuned for the other concern.
"""
from __future__ import annotations

from decimal import Decimal
from typing import NamedTuple

from risk.cost_model import total_expected_cost

DEFAULT_RISK_PCT = Decimal("0.0025")  # 0.25% — the brief's explicit Kraken Prop default


class _CostInput(NamedTuple):
    """Duck-typed input for risk.cost_model.total_expected_cost."""
    notional: Decimal
    expected_hold_hours: Decimal


def derive_size(equity: Decimal, entry: Decimal, stop: Decimal, risk_pct: Decimal = DEFAULT_RISK_PCT) -> Decimal:
    """(equity * risk_pct) / abs(entry - stop). The only sizing formula —
    never a chosen or allocation-derived number, and never a function of
    leverage."""
    stop_distance = abs(entry - stop)
    if stop_distance <= 0:
        raise ValueError("stop must differ from entry")
    return (equity * risk_pct) / stop_distance


def derive_notional(size: Decimal, entry: Decimal) -> Decimal:
    return size * entry


def derive_leverage_required(notional: Decimal, equity: Decimal) -> Decimal:
    """Margin-efficiency readout only — never an input to derive_size."""
    if equity <= 0:
        return Decimal("0")
    return notional / equity


def derive_expected_cost(notional: Decimal, expected_hold_hours: Decimal) -> Decimal:
    return total_expected_cost(_CostInput(notional=notional, expected_hold_hours=expected_hold_hours))


def derive_worst_case_loss(size: Decimal, entry: Decimal, stop: Decimal, expected_cost: Decimal) -> Decimal:
    """(size * abs(entry - stop)) + expected_cost — the stop-loss outcome
    plus costs paid regardless of outcome."""
    return (size * abs(entry - stop)) + expected_cost


def derive_r_multiple(entry: Decimal, stop: Decimal, target: Decimal, size: Decimal, expected_cost: Decimal) -> Decimal:
    """(target - entry) / (entry - stop), cost-adjusted: expected_cost is
    converted to a per-unit price-equivalent (expected_cost / size) and
    subtracted from the raw reward before dividing by risk, since real costs
    reduce the take-profit's actual realized payoff."""
    stop_distance = abs(entry - stop)
    if stop_distance <= 0:
        raise ValueError("stop must differ from entry")
    reward = abs(target - entry)
    cost_per_unit = expected_cost / size if size > 0 else Decimal("0")
    return (reward - cost_per_unit) / stop_distance
