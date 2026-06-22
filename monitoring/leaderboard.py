"""Leaderboard — queries engine_results and ranks strategy-symbol combinations."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from portfolio.optimizer import PortfolioOptimizer

logger = logging.getLogger(__name__)

# Minimum fraction of windows that must pass (positive Sharpe, sane drawdown)
# for a strategy-symbol combo to be considered "working." Strategies below
# this still appear on the leaderboard (with a reason) so it's visible what
# doesn't work and why, not just what does.
MIN_CONSISTENCY = 0.50


def _score(sharpe: float, win_rate: float, consistency: float) -> float:
    return sharpe * win_rate * consistency


def _failure_reason(strategy_name: str, results: Dict) -> str:
    """Ask the strategy itself why it likely failed (BaseStrategy.describe_failure),
    falling back to a generic message if the strategy isn't registered or
    doesn't override it."""
    try:
        from strategies.registry import StrategyRegistry
        strategy = StrategyRegistry.instantiate(strategy_name)
        return strategy.describe_failure(results)
    except Exception:
        return (
            f"pass_ratio={results.get('pass_ratio', 0):.2f} below the "
            f"{MIN_CONSISTENCY:.0%} consistency floor "
            f"(sharpe={results.get('sharpe_ratio', 0):.2f})"
        )


def _fetch_returns_history(db) -> pd.DataFrame:
    """Return per-window total_return_pct pivoted by strategy|symbol key."""
    try:
        df = db.read_sql("""
            SELECT strategy_name, symbol, params, window_end, total_return_pct
            FROM engine_results
            WHERE bars_used IS NOT NULL
            ORDER BY window_end
        """)
    except Exception as e:
        logger.warning("Could not fetch returns history for risk-parity: %s", e)
        return pd.DataFrame()

    if df.empty:
        return df

    df["params"] = df["params"].apply(
        lambda p: json.dumps(p, sort_keys=True) if isinstance(p, dict) else (p or "{}")
    )
    df["key"] = df["strategy_name"] + "|" + df["symbol"] + "|" + df["params"]
    pivot = df.pivot_table(
        index="window_end", columns="key", values="total_return_pct", aggfunc="mean"
    )
    return pivot


def _add_risk_parity_allocations(result: pd.DataFrame, returns_pivot: pd.DataFrame) -> pd.DataFrame:
    """Append a risk_parity_alloc_pct column to the leaderboard DataFrame.

    Uses PortfolioOptimizer.multi_strategy_allocate with the return time series
    from engine_results for qualifying strategies.  Falls back to equal-weight
    when there is insufficient history.
    """
    if result.empty:
        return result

    result["key"] = (
        result["strategy_name"] + "|" + result["symbol"] + "|"
        + result["params"].apply(lambda p: json.dumps(p, sort_keys=True) if isinstance(p, dict) else str(p))
    )
    strategy_keys = result["key"].tolist()

    optimizer = PortfolioOptimizer(initial_capital=1.0, max_position_size=1.0)
    allocations = optimizer.multi_strategy_allocate(
        strategy_names=strategy_keys,
        returns_history=returns_pivot,
        capital=1.0,
        method="risk_parity" if not returns_pivot.empty else "equal_weight",
    )

    result["risk_parity_alloc_pct"] = result["key"].map(
        lambda k: round(allocations.get(k, 0.0) * 100, 1)
    )
    result = result.drop(columns=["key"])
    return result


def build_leaderboard(db, tier: Optional[str] = None) -> pd.DataFrame:
    """Query engine_results and return a ranked leaderboard DataFrame."""
    try:
        df = db.read_sql("""
            SELECT
                strategy_name,
                symbol,
                params,
                window_type,
                sharpe_ratio,
                max_drawdown_pct,
                win_rate_pct,
                num_trades,
                conservative_pass,
                standard_pass,
                permissive_pass,
                regime_trend,
                regime_volatility,
                regime_direction,
                bars_used
            FROM engine_results
            WHERE bars_used IS NOT NULL
        """)
    except Exception as e:
        logger.error("Failed to query engine_results: %s", e)
        return pd.DataFrame()

    if df.empty:
        logger.warning("engine_results is empty — run: python main.py engine first.")
        return pd.DataFrame()

    records: List[Dict] = []

    df["params"] = df["params"].apply(
        lambda p: json.dumps(p, sort_keys=True) if isinstance(p, dict) else (p or "{}")
    )

    for (strategy_name, symbol, params_json), group in df.groupby(
        ["strategy_name", "symbol", "params"]
    ):
        n_total = len(group)
        if n_total == 0:
            continue

        # permissive_pass is the least restrictive per-window flag already
        # computed by the engine (positive Sharpe, sane drawdown) — used as
        # the single consistency measure now that there's no tier system.
        n_pass = int(group["permissive_pass"].sum())
        pass_ratio = n_pass / n_total

        avg_sharpe = float(group["sharpe_ratio"].mean())
        avg_dd = float(group["max_drawdown_pct"].mean())
        avg_wr = float(group["win_rate_pct"].mean())
        total_num_trades = int(group["num_trades"].sum())

        # Dominant regime description
        trend_avg = float(group["regime_trend"].mean())
        vol_avg = float(group["regime_volatility"].mean())

        trend_label = "trending" if trend_avg > 0.7 else ("mixed" if trend_avg > 0.3 else "ranging")
        vol_label = "high-vol" if vol_avg > 5.0 else ("normal-vol" if vol_avg > 2.0 else "low-vol")
        regime_label = f"{trend_label} + {vol_label}"

        try:
            params_dict = json.loads(params_json) if isinstance(params_json, str) else params_json
        except Exception:
            params_dict = {}

        qualifies = pass_ratio >= MIN_CONSISTENCY
        score = _score(avg_sharpe, avg_wr, pass_ratio) if qualifies else 0.0
        reason = "" if qualifies else _failure_reason(strategy_name, {
            "num_trades": total_num_trades,
            "sharpe_ratio": avg_sharpe,
            "max_drawdown_pct": avg_dd,
            "win_rate_pct": avg_wr,
            "pass_ratio": pass_ratio,
        })

        records.append({
            "strategy_name": strategy_name,
            "symbol": symbol,
            "params": params_dict,
            "avg_sharpe": round(avg_sharpe, 3),
            "avg_max_dd_pct": round(avg_dd, 2),
            "avg_win_rate_pct": round(avg_wr, 1),
            "n_windows": n_total,
            "total_num_trades": total_num_trades,
            "pass_ratio": round(pass_ratio, 3),
            "regime": regime_label,
            "score": round(score, 4),
            "qualifies": qualifies,
            "reason": reason,
        })

    if not records:
        logger.warning("No engine_results rows to build a leaderboard from.")
        return pd.DataFrame()

    result = pd.DataFrame(records)

    # tier kept as a CLI-compatible filter: any truthy value means
    # "qualifying only," since there's a single bar now rather than tiers.
    if tier:
        result = result[result["qualifies"]]

    result = result.sort_values(["qualifies", "score"], ascending=[False, False])
    result = result.reset_index(drop=True)
    result.index += 1

    returns_pivot = _fetch_returns_history(db)
    result = _add_risk_parity_allocations(result, returns_pivot)

    return result


def print_leaderboard(lb: pd.DataFrame, tier: Optional[str] = None) -> None:
    if lb.empty:
        print("No leaderboard results available.")
        return

    label = "QUALIFYING ONLY" if tier else "ALL STRATEGIES x SYMBOLS"
    print("=" * 100)
    print(f"STRATEGY LEADERBOARD — {label}")
    print("=" * 100)

    header = (
        f"{'#':>4} | {'Strategy':25} | {'Symbol':10} | {'Sharpe':7} | {'MaxDD':6} | "
        f"{'WinRate':7} | {'Windows':7} | {'Pass%':6} | {'Regime'}"
    )
    print(header)
    print("-" * len(header))

    for rank, row in lb.iterrows():
        params_str = ", ".join(f"{k}={v}" for k, v in row["params"].items()) if row["params"] else ""
        status = "OK" if row["qualifies"] else "FAIL"
        alloc = row.get("risk_parity_alloc_pct", 0.0)
        print(
            f"{rank:>4} | {row['strategy_name']:25} | {row['symbol']:10} | "
            f"{row['avg_sharpe']:7.2f} | {row['avg_max_dd_pct']:6.1f} | "
            f"{row['avg_win_rate_pct']:7.1f} | {row['n_windows']:7} | "
            f"{row['pass_ratio']*100:5.1f}% | [{status}] alloc={alloc:.1f}% {row['regime']}"
        )
        if params_str:
            print(f"{'':4}   params: {params_str}")
        if row["reason"]:
            print(f"{'':4}   why: {row['reason']}")

    print("=" * 100)
    print(f"Qualifying: {int(lb['qualifies'].sum())} / {len(lb)} strategy-symbol combos")


def write_leaderboard_md(lb: pd.DataFrame, tier: Optional[str] = None) -> None:
    """Write leaderboard to LEADERBOARD.md in project root."""
    project_root = Path(__file__).resolve().parent.parent
    out_path = project_root / "LEADERBOARD.md"

    label = "QUALIFYING ONLY" if tier else "ALL STRATEGIES x SYMBOLS"
    lines = [
        f"# Raf3nd Strategy Leaderboard — {label}",
        "",
        "| Rank | Strategy | Symbol | Sharpe | MaxDD | WinRate | Windows | Pass% | Status | Regime | Why (if failing) |",
        "|------|----------|--------|--------|-------|---------|---------|-------|--------|--------|-------------------|",
    ]

    for rank, row in lb.iterrows():
        params_str = str(row["params"]) if row["params"] else ""
        status = "OK" if row["qualifies"] else "FAIL"
        lines.append(
            f"| {rank} | {row['strategy_name']} `{params_str}` | {row['symbol']} | "
            f"{row['avg_sharpe']:.2f} | {row['avg_max_dd_pct']:.1f}% | "
            f"{row['avg_win_rate_pct']:.1f}% | {row['n_windows']} | "
            f"{row['pass_ratio']*100:.1f}% | {status} | {row['regime']} | {row['reason']} |"
        )

    out_path.write_text("\n".join(lines) + "\n")
    logger.info("Leaderboard written to %s", out_path)


def run_leaderboard_cmd(db, tier: Optional[str] = None) -> bool:
    """Entry point called from main.py leaderboard command."""
    lb = build_leaderboard(db, tier=tier)
    if lb.empty:
        if tier:
            print("No strategy-symbol combos currently qualify. Run without --tier to see all results and why they failed.")
        else:
            print("No engine_results yet. Run: python main.py engine")
        return False

    print_leaderboard(lb, tier=tier)
    write_leaderboard_md(lb, tier=tier)

    # ML classifier recommendation section
    classifier_path = Path(__file__).resolve().parent.parent / "models" / "regime_classifier.pkl"
    if classifier_path.exists():
        try:
            _print_classifier_recommendations(lb)
        except Exception as e:
            logger.warning("Classifier recommendation failed: %s", e)
    else:
        print()
        print("Run `python main.py train-classifier` once engine_results has 200+ rows to enable regime recommendations.")

    return True


def _print_classifier_recommendations(lb: pd.DataFrame) -> None:
    """Print top strategies recommended for the current market regime."""
    import pickle
    from pathlib import Path

    classifier_path = Path(__file__).resolve().parent.parent / "models" / "regime_classifier.pkl"
    with open(classifier_path, "rb") as f:
        clf_bundle = pickle.load(f)

    model = clf_bundle.get("model")
    feature_names = clf_bundle.get("feature_names", [])

    if model is None or not lb.empty:
        print()
        print("=== Recommended Strategies for Current Market Regime ===")
        top5 = lb.head(5)
        for rank, row in top5.iterrows():
            status = "OK" if row["qualifies"] else "FAIL"
            print(f"  {rank}. {row['strategy_name']} on {row['symbol']} [{status}] — {row['regime']}")
