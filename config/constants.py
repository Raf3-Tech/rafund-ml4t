"""Project-wide numerical constants.

Centralizes conventions that must stay consistent across the backtest engine,
significance testing, model training, portfolio analytics, and monitoring.
"""

from __future__ import annotations

# Annualization factor for return statistics (Sharpe, Sortino, volatility).
#
# Raf3nd trades crypto, which is a 24/7/365 market — there are ~365 return
# observations per year on daily bars, not the ~252 trading days used for
# equities. Annualizing with 252 understates the volatility scaling and
# mis-calibrates every Sharpe gate. Use this constant everywhere instead of a
# hard-coded literal so the convention is single-sourced and auditable.
PERIODS_PER_YEAR: int = 365

# Default annual risk-free rate used when computing excess-return Sharpe/Sortino.
RISK_FREE_RATE_ANNUAL: float = 0.0

# Canonical z-score thresholds for the stat-arb strategy.
# These are the single source of truth for all constructor defaults and
# config loader fallbacks. Import here instead of writing the literal 2.0 / 0.5
# anywhere else — one change here propagates everywhere.
STAT_ARB_ENTRY_Z: float = 2.0
STAT_ARB_EXIT_Z: float = 0.5
