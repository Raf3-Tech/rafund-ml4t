"""Read-only adapter onto the Kraken Prop position slot (trading.position).

Kraken Prop trades one account, one position at a time (run_id="live_{exchange}")
— not the multi-slot paper-trading model trading.position.list_positions also
supports (state/repository_map.md Section 6). This wraps the exact
load_position() call signals.pending_signal_detector.scan_kraken_signals
itself uses, so Senior Trader never builds a second position-loading path;
senior_trader.adapters.account builds on this rather than loading its own
copy of the same PositionState.
"""
from __future__ import annotations

from trading.position import PositionState


def current_position(db, exchange: str = "kraken") -> PositionState:
    from trading.position import load_position

    return load_position(db, f"live_{exchange}", strategy_name="", symbol="", exchange=exchange)
