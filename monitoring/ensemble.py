"""Ensemble signal layer for Raf3nd ML4T.

Implements the "Trust-The-Majority" pattern from IEEE 11513234
("Enhancing Cryptocurrency Trading Strategies: A DRL Approach Integrating
Multi-Source LLM Sentiment Analysis").

Architecture
------------
Rather than selecting a single best strategy per regime tier, the ensemble
queries the top-N strategies from the leaderboard for the requested tier and
emits a majority-vote composite signal.  The voting logic:
  - Each strategy in the top-N votes BUY (+1), SELL (-1), or HOLD (0).
  - The composite signal is the sign of the sum of votes.
  - Ties (net = 0) resolve to HOLD.

This is intentionally kept as a post-processing layer (no engine modification)
so it satisfies Rule 1: the engine's single P&L loop remains the only authority
on signal-driven equity computation.

Usage
-----
    from monitoring.ensemble import build_ensemble_signal

    # Given a leaderboard DataFrame and a per-strategy signal dict:
    composite = build_ensemble_signal(leaderboard_df, strategy_signals, top_n=3)

The ``strategy_signals`` dict maps ``strategy_name`` → ``pd.Series`` of
"BUY"/"SELL"/"HOLD" strings (indexed by timestamp or integer bar index).
"""

from __future__ import annotations

import logging
from typing import Dict, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# Mapping from string signal to integer vote
_VOTE = {"BUY": 1, "SELL": -1, "HOLD": 0}
_VOTE_REVERSE = {1: "BUY", -1: "SELL", 0: "HOLD"}


def build_ensemble_signal(
    leaderboard: pd.DataFrame,
    strategy_signals: Dict[str, pd.Series],
    top_n: int = 3,
    tier: Optional[str] = None,
) -> pd.Series:
    """Majority-vote composite signal from the top-N leaderboard strategies.

    Parameters
    ----------
    leaderboard:
        DataFrame produced by ``monitoring.leaderboard.build_leaderboard``.
        Expected columns: ``strategy_name``, ``best_tier``, and the tier score
        columns (``score_conservative``, ``score_standard``, ``score_permissive``).
    strategy_signals:
        Mapping of strategy_name → pd.Series of "BUY"/"SELL"/"HOLD" strings.
        All series should share the same index (bar timestamps or integers).
    top_n:
        Number of top-ranked strategies to include in the vote.  Strategies
        not present in ``strategy_signals`` are silently skipped.
    tier:
        Optional tier filter ("CONSERVATIVE", "STANDARD", "PERMISSIVE").
        When provided, only strategies that qualify for this tier participate.
        When None, all qualifying strategies are considered.

    Returns
    -------
    pd.Series
        Composite signal ("BUY", "SELL", or "HOLD") indexed identically to the
        input signal series.  Returns a flat HOLD series when fewer than 1
        participating strategy is available.

    Notes
    -----
    Tie-breaking: when the vote sum is exactly 0 (equal BUY and SELL votes),
    the composite is HOLD.  This is deliberately conservative — we prefer
    sitting flat over acting on a contested signal.
    """
    if leaderboard is None or leaderboard.empty:
        logger.warning("Ensemble: empty leaderboard — returning HOLD series.")
        # Return a minimal HOLD series if we have at least one signal series
        if strategy_signals:
            ref = next(iter(strategy_signals.values()))
            return pd.Series("HOLD", index=ref.index)
        return pd.Series(dtype=str)

    lb = leaderboard.copy()

    # Filter by tier
    if tier is not None:
        tier_upper = tier.upper()
        col_map = {
            "CONSERVATIVE": "qualifies_conservative",
            "STANDARD": "qualifies_standard",
            "PERMISSIVE": "qualifies_permissive",
        }
        col = col_map.get(tier_upper)
        if col and col in lb.columns:
            lb = lb[lb[col]]
        score_col = f"score_{tier_upper.lower()}" if tier else "score_conservative"
    else:
        score_col = "score_conservative"

    if score_col not in lb.columns:
        score_col = "avg_sharpe"  # fallback

    # Sort by score descending and take top_n
    lb = lb.sort_values(score_col, ascending=False).head(top_n)

    # Identify which strategies we actually have signals for
    participating: list[str] = []
    for _, row in lb.iterrows():
        sname = row["strategy_name"]
        if sname in strategy_signals and not strategy_signals[sname].empty:
            participating.append(sname)

    if not participating:
        logger.warning(
            "Ensemble: none of the top-%d leaderboard strategies have matching signals.", top_n
        )
        if strategy_signals:
            ref = next(iter(strategy_signals.values()))
            return pd.Series("HOLD", index=ref.index)
        return pd.Series(dtype=str)

    logger.info("Ensemble: voting from %d strategies: %s", len(participating), participating)

    # Align all signal series to a common index
    ref_index = strategy_signals[participating[0]].index
    vote_df = pd.DataFrame(index=ref_index)
    for sname in participating:
        sig = strategy_signals[sname].reindex(ref_index).fillna("HOLD")
        vote_df[sname] = sig.map(_VOTE).fillna(0).astype(int)

    # Majority vote: sum of votes, sign gives direction; 0 → HOLD
    vote_sum = vote_df.sum(axis=1)
    composite = vote_sum.apply(lambda v: _VOTE_REVERSE[1 if v > 0 else (-1 if v < 0 else 0)])
    composite.name = "ensemble_signal"

    buy_pct = (composite == "BUY").mean() * 100
    sell_pct = (composite == "SELL").mean() * 100
    hold_pct = (composite == "HOLD").mean() * 100
    logger.info(
        "Ensemble result: BUY=%.1f%% SELL=%.1f%% HOLD=%.1f%%",
        buy_pct, sell_pct, hold_pct,
    )

    return composite


def ensemble_agreement_rate(
    leaderboard: pd.DataFrame,
    strategy_signals: Dict[str, pd.Series],
    top_n: int = 3,
) -> float:
    """Fraction of bars where all top-N strategies agree (unanimous vote).

    A high agreement rate indicates regime clarity; a low rate suggests
    regime ambiguity where the ensemble's diversification benefit is largest.

    Returns
    -------
    float in [0.0, 1.0]. Returns 0.0 when fewer than 2 strategies participate.
    """
    if leaderboard is None or leaderboard.empty or not strategy_signals:
        return 0.0

    lb = leaderboard.sort_values("score_conservative", ascending=False).head(top_n)
    participating = [
        row["strategy_name"]
        for _, row in lb.iterrows()
        if row["strategy_name"] in strategy_signals
    ]

    if len(participating) < 2:
        return 0.0

    ref_index = strategy_signals[participating[0]].index
    vote_df = pd.DataFrame(index=ref_index)
    for sname in participating:
        sig = strategy_signals[sname].reindex(ref_index).fillna("HOLD")
        vote_df[sname] = sig.map(_VOTE).fillna(0).astype(int)

    # Unanimous: all votes equal
    unanimous = (vote_df.nunique(axis=1) == 1)
    return float(unanimous.mean())
