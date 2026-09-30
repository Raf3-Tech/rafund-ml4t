"""Read-only adapter onto engine_results — raw walk-forward window rows for
one strategy x symbol, un-aggregated.

monitoring.leaderboard.build_leaderboard aggregates these across windows
(mean Sharpe, pass_ratio, etc. — see senior_trader.adapters.research and
.dsr for the aggregated view). This adapter exists for the window-level
detail that aggregation discards, e.g. reviewing whether a candidate's most
recent windows are trending down even though its lifetime average still
looks fine (AGENT_ORCHESTRATION.md Section 6's "walk-forward skepticism").

Mirrors, rather than alters, the exact column set and `bars_used IS NOT
NULL` filter monitoring/leaderboard.py's own engine_results query uses
(build_leaderboard, ~line 152), so a window this adapter shows and a window
the leaderboard aggregated are guaranteed to be the same underlying
population. `engine_results` IS this codebase's walk-forward result store —
there is no separate "backtest results" table distinct from it (README's
Module Tree; backtesting/engine.py's single-asset runs feed the same
table), so "backtest results" and "walk-forward results" in
AGENT_ORCHESTRATION.md Section 50's adapter list are the same underlying
data here, not two separate reads.
"""
from __future__ import annotations

from typing import List


def walk_forward_windows(db, strategy_name: str, symbol: str, limit: int = 50) -> List[dict]:
    df = db.read_sql(
        """
        SELECT strategy_name, symbol, params, window_type, window_end,
               sharpe_ratio, max_drawdown_pct, win_rate_pct, num_trades,
               conservative_pass, standard_pass, permissive_pass,
               regime_trend, regime_volatility, regime_direction, bars_used
        FROM engine_results
        WHERE bars_used IS NOT NULL AND strategy_name = %s AND symbol = %s
        ORDER BY window_end DESC
        LIMIT %s
        """,
        params=[strategy_name, symbol, limit],
    )
    return df.to_dict("records") if not df.empty else []
