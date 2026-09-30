"""Read-only adapter onto the Kraken Prop account-state authority.

Delegates to signals.pending_signal_detector.current_account_state — the
single function both risk/pretrade_gate.py's caller (scan_kraken_signals)
and monitoring/routes/publish.py already share (state/repository_map.md
Section 5), so Senior Trader can never report a different account-state
number than the gate or the tablet did. Does not build a second
PropAccountState from scratch — and builds on senior_trader.adapters.
positions.current_position for the underlying PositionState rather than
loading a second copy of it.
"""
from __future__ import annotations

from risk.prop_account import PropAccountState


def current_state(db, exchange: str = "kraken") -> PropAccountState:
    from config.loader import get_settings
    from senior_trader.adapters.positions import current_position
    from signals.pending_signal_detector import current_account_state

    pos = current_position(db, exchange=exchange)
    cfg = get_settings()
    return current_account_state(pos, cfg)
