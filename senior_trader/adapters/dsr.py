"""Read-only adapter exposing DSR/overfitting-correction metrics
(monitoring/overfitting.py, wired into monitoring/leaderboard.py's Phase-6
pass) for one strategy x symbol — see state/repository_map.md Section 4 for
what each field means and where zero eligible strategies stood as of the
last check.

A thin projection of senior_trader.adapters.research.leaderboard_row, not a
second DSR computation — AGENT_ORCHESTRATION.md Section 3's "never
reproduce/approximate the gate logic" applies with equal force to
monitoring/overfitting.py's math: Senior Trader must never compute a second
deflated Sharpe ratio.
"""
from __future__ import annotations

from typing import Optional

_DSR_FIELDS = (
    "passes_overfitting_gate",
    "deflated_sharpe",
    "dsr_z_score",
    "dsr_expected_max_sharpe_null",
    "dsr_n_trials",
    "dsr_n_observations",
    "dsr_skew",
    "dsr_kurtosis",
    "dsr_underflowed",
    "oos_within_confidence_band",
    "trial_count",
)


def dsr_metrics(db, strategy_name: str, symbol: str) -> Optional[dict]:
    """None if the (strategy, symbol) combo isn't on the leaderboard at
    all; otherwise a dict of exactly the DSR-related fields, including
    `dsr_underflowed` (see monitoring/overfitting.py's DeflatedSharpeResult
    docstring for why a floored 0.0 probability and a genuinely-zero
    probability must stay distinguishable)."""
    from senior_trader.adapters.research import leaderboard_row

    row = leaderboard_row(db, strategy_name, symbol)
    if row is None:
        return None
    return {field: row.get(field) for field in _DSR_FIELDS}
