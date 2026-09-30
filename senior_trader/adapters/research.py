"""Read-only adapter onto the leaderboard's eligibility pipeline.

Reuses signals.pending_signal_detector._qualifying_single_symbol_candidates
rather than reimplementing the qualifies/passes_overfitting_gate/prop_eligible
filter a second time — see state/repository_map.md Section 4 for exactly
what that filter checks, and AGENTS.md's Rule 1 for why this repo never
duplicates a code path that already exists. The leading underscore marks
that function module-private by convention, not by enforcement; this is a
deliberate, documented reuse, not an accident.
"""
from __future__ import annotations

from typing import List, Optional, Tuple


def eligible_candidates(db) -> List[Tuple[int, str, str, dict, float]]:
    """(rank, strategy_name, symbol, params, score) tuples for every
    strategy x symbol combination currently eligible to reach the pre-trade
    gate — identical to what
    signals.pending_signal_detector.scan_kraken_signals itself iterates
    over. Read-only: calls no write path."""
    from signals.pending_signal_detector import _qualifying_single_symbol_candidates

    return _qualifying_single_symbol_candidates(db)


def leaderboard_row(db, strategy_name: str, symbol: str) -> Optional[dict]:
    """Full leaderboard row (every computed column, including the DSR
    fields senior_trader.adapters.dsr projects) for one strategy x symbol,
    or None if not present. Calls build_leaderboard() fresh each time —
    Phase 2 doesn't cache within a request, same as the CLI/dashboard's own
    repeated calls to it today."""
    from monitoring.leaderboard import build_leaderboard

    lb = build_leaderboard(db)
    if lb.empty:
        return None
    match = lb[(lb["strategy_name"] == strategy_name) & (lb["symbol"] == symbol)]
    if match.empty:
        return None
    return match.iloc[0].to_dict()
