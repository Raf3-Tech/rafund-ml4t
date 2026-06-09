"""Closed-loop research pipeline.

Flow:
  1. propose  — select a strategy + symbol to investigate (from registry or leaderboard)
  2. campaign  — run the walk-forward engine on that target
  3. evaluate  — pull engine_results, score via leaderboard + statistical gates
  4. decide    — accept / reject / watch based on tier qualification + Sharpe floor
  5. log       — append the decision to tmp/research_decisions.jsonl + MLflow tags

CLI entry point (via main.py):
    python main.py research --strategy "EMA Crossover" --symbol BTC/USDT
    python main.py research --top-n 3 --tier conservative
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)

# Acceptance floors — strategies that clear these are promoted.
_SHARPE_FLOOR = 0.3
_TIER_CONSISTENCY_FLOORS = {
    "CONSERVATIVE": 0.70,   # 70% win-probability target for the 1-phase prop challenge
    "STANDARD": 0.60,
    "PERMISSIVE": 0.50,
}
_DECISIONS_LOG = Path("tmp") / "research_decisions.jsonl"


@dataclass
class ResearchDecision:
    timestamp: str
    strategy_name: str
    symbol: Optional[str]
    tier: str
    accepted: bool
    reason: str
    avg_sharpe: float
    avg_max_dd_pct: float
    avg_win_rate_pct: float
    cons_ratio: float
    std_ratio: float
    perm_ratio: float
    n_windows: int
    params: Dict


def _gate(row: Dict) -> ResearchDecision:
    """Apply acceptance gate to a single leaderboard row dict."""
    name = row.get("strategy_name", "")
    symbol = row.get("symbol")
    avg_sharpe = float(row.get("avg_sharpe", 0.0))
    avg_dd = float(row.get("avg_max_dd_pct", 0.0))
    avg_wr = float(row.get("avg_win_rate_pct", 0.0))
    cons_ratio = float(row.get("cons_ratio", 0.0))
    std_ratio = float(row.get("std_ratio", 0.0))
    perm_ratio = float(row.get("perm_ratio", 0.0))
    n_windows = int(row.get("n_windows", 0))
    params = row.get("params") or {}

    # Determine highest qualifying tier
    tier = "NONE"
    if cons_ratio >= _TIER_CONSISTENCY_FLOORS["CONSERVATIVE"] and avg_sharpe >= _SHARPE_FLOOR:
        tier = "CONSERVATIVE"
    elif std_ratio >= _TIER_CONSISTENCY_FLOORS["STANDARD"] and avg_sharpe >= _SHARPE_FLOOR:
        tier = "STANDARD"
    elif perm_ratio >= _TIER_CONSISTENCY_FLOORS["PERMISSIVE"] and avg_sharpe >= _SHARPE_FLOOR:
        tier = "PERMISSIVE"

    accepted = tier != "NONE"

    if accepted:
        reason = (
            f"Qualifies for {tier} tier: "
            f"consistency={cons_ratio if tier == 'CONSERVATIVE' else (std_ratio if tier == 'STANDARD' else perm_ratio):.2f}, "
            f"avg_sharpe={avg_sharpe:.2f}"
        )
    else:
        reason = (
            f"Rejected: best consistency "
            f"(cons={cons_ratio:.2f}, std={std_ratio:.2f}, perm={perm_ratio:.2f}) "
            f"or Sharpe ({avg_sharpe:.2f}) below floor ({_SHARPE_FLOOR})"
        )

    return ResearchDecision(
        timestamp=datetime.now(timezone.utc).isoformat(),
        strategy_name=name,
        symbol=symbol,
        tier=tier,
        accepted=accepted,
        reason=reason,
        avg_sharpe=avg_sharpe,
        avg_max_dd_pct=avg_dd,
        avg_win_rate_pct=avg_wr,
        cons_ratio=cons_ratio,
        std_ratio=std_ratio,
        perm_ratio=perm_ratio,
        n_windows=n_windows,
        params=params,
    )


def _log_decision(decision: ResearchDecision) -> None:
    _DECISIONS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with _DECISIONS_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(decision)) + "\n")
    logger.info(
        "research_decision_logged",
        strategy=decision.strategy_name,
        symbol=decision.symbol,
        tier=decision.tier,
        accepted=decision.accepted,
    )


def run_research_pipeline(
    db,
    strategy_filter: Optional[str] = None,
    symbol_filter: Optional[str] = None,
    top_n: int = 5,
    tier_filter: Optional[str] = None,
    dry_run: bool = False,
) -> List[ResearchDecision]:
    """Run the full research loop and return a list of decisions.

    Args:
        db: DatabaseConnection
        strategy_filter: Restrict engine run to this strategy name.
        symbol_filter: Restrict engine run to this symbol.
        top_n: Number of leaderboard candidates to gate when no filter given.
        tier_filter: Leaderboard tier to draw candidates from.
        dry_run: If True, skip the engine run (re-use existing engine_results).
    """
    from backtesting.window_engine import WalkForwardWindowEngine
    from config.loader import get_settings
    from monitoring.leaderboard import build_leaderboard
    import strategies as _strat_pkg  # noqa: F401 — populates registry
    from strategies.registry import StrategyRegistry

    decisions: List[ResearchDecision] = []

    # --- Step 1: campaign (engine run) ---
    if not dry_run:
        logger.info("research_pipeline_campaign_start", strategy=strategy_filter, symbol=symbol_filter)
        cfg = get_settings()
        symbols = db.get_symbols_with_data()
        if not symbols:
            logger.error("No price data — run: python main.py collect first")
            return decisions

        strategy_instances = StrategyRegistry.instantiate_all()
        engine = WalkForwardWindowEngine(
            db=db,
            strategies=strategy_instances,
            symbols=symbols,
            commission=cfg.commission,
        )
        results = engine.run(strategy_filter=strategy_filter, symbol_filter=symbol_filter)
        logger.info("research_pipeline_campaign_done", n_results=len(results))

    # --- Step 2: evaluate (leaderboard) ---
    lb = build_leaderboard(db, tier=tier_filter)
    if lb.empty:
        logger.warning("research_pipeline_no_leaderboard_results")
        return decisions

    # Filter to the requested strategy/symbol if specified
    if strategy_filter:
        lb = lb[lb["strategy_name"].str.lower() == strategy_filter.lower()]
    if symbol_filter:
        lb = lb[lb["symbol"] == symbol_filter]

    # Take top-N candidates
    candidates = lb.head(top_n)

    # --- Step 3: gate + log ---
    accepted_count = 0
    for _, row in candidates.iterrows():
        decision = _gate(row.to_dict())
        _log_decision(decision)
        decisions.append(decision)
        if decision.accepted:
            accepted_count += 1

    logger.info(
        "research_pipeline_complete",
        candidates_evaluated=len(decisions),
        accepted=accepted_count,
        rejected=len(decisions) - accepted_count,
    )
    return decisions


def print_research_summary(decisions: List[ResearchDecision]) -> None:
    if not decisions:
        print("No candidates evaluated.")
        return

    print("=" * 90)
    print("RESEARCH PIPELINE SUMMARY")
    print("=" * 90)
    accepted = [d for d in decisions if d.accepted]
    rejected = [d for d in decisions if not d.accepted]

    if accepted:
        print(f"\nACCEPTED ({len(accepted)}):")
        for d in accepted:
            print(f"  + {d.strategy_name} / {d.symbol or 'all'} → {d.tier}  "
                  f"(Sharpe={d.avg_sharpe:.2f}, cons={d.cons_ratio:.2f}, "
                  f"std={d.std_ratio:.2f}, windows={d.n_windows})")

    if rejected:
        print(f"\nREJECTED ({len(rejected)}):")
        for d in rejected:
            print(f"  - {d.strategy_name} / {d.symbol or 'all'}  {d.reason}")

    print(f"\nDecisions logged to: {_DECISIONS_LOG.resolve()}")
    print("=" * 90)
