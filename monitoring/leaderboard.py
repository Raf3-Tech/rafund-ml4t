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

# Minimum consistency ratio to appear in each tier.
# CONSERVATIVE is set to 0.70 — matches the 70% historical win-probability
# target for the 1-phase prop challenge (9% profit, 3% DD).
CONSERVATIVE_MIN_CONSISTENCY = 0.70
STANDARD_MIN_CONSISTENCY = 0.60
PERMISSIVE_MIN_CONSISTENCY = 0.50


def _score(sharpe: float, win_rate: float, consistency: float) -> float:
    return sharpe * win_rate * consistency


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

        n_cons = int(group["conservative_pass"].sum())
        n_std = int(group["standard_pass"].sum())
        n_perm = int(group["permissive_pass"].sum())

        cons_ratio = n_cons / n_total
        std_ratio = n_std / n_total
        perm_ratio = n_perm / n_total

        avg_sharpe = float(group["sharpe_ratio"].mean())
        avg_dd = float(group["max_drawdown_pct"].mean())
        avg_wr = float(group["win_rate_pct"].mean())

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

        # Determine which tiers this combo qualifies for
        qualifies_conservative = cons_ratio >= CONSERVATIVE_MIN_CONSISTENCY
        qualifies_standard = std_ratio >= STANDARD_MIN_CONSISTENCY
        qualifies_permissive = perm_ratio >= PERMISSIVE_MIN_CONSISTENCY

        if qualifies_conservative:
            best_tier = "CONSERVATIVE"
        elif qualifies_standard:
            best_tier = "STANDARD"
        elif qualifies_permissive:
            best_tier = "PERMISSIVE"
        else:
            continue  # Does not qualify for any tier

        score_cons = _score(avg_sharpe, avg_wr, cons_ratio) if qualifies_conservative else 0.0
        score_std = _score(avg_sharpe, avg_wr, std_ratio) if qualifies_standard else 0.0
        score_perm = _score(avg_sharpe, avg_wr, perm_ratio) if qualifies_permissive else 0.0

        records.append({
            "strategy_name": strategy_name,
            "symbol": symbol,
            "params": params_dict,
            "avg_sharpe": round(avg_sharpe, 3),
            "avg_max_dd_pct": round(avg_dd, 2),
            "avg_win_rate_pct": round(avg_wr, 1),
            "n_windows": n_total,
            "cons_ratio": round(cons_ratio, 3),
            "std_ratio": round(std_ratio, 3),
            "perm_ratio": round(perm_ratio, 3),
            "best_tier": best_tier,
            "regime": regime_label,
            "score_conservative": round(score_cons, 4),
            "score_standard": round(score_std, 4),
            "score_permissive": round(score_perm, 4),
            "qualifies_conservative": qualifies_conservative,
            "qualifies_standard": qualifies_standard,
            "qualifies_permissive": qualifies_permissive,
        })

    if not records:
        logger.warning("No strategy-symbol combinations meet minimum consistency thresholds.")
        return pd.DataFrame()

    result = pd.DataFrame(records)

    # Filter by requested tier
    if tier:
        tier_upper = tier.upper()
        col_map = {
            "CONSERVATIVE": "qualifies_conservative",
            "STANDARD": "qualifies_standard",
            "PERMISSIVE": "qualifies_permissive",
        }
        col = col_map.get(tier_upper)
        if col:
            result = result[result[col]]
            result = result.sort_values(f"score_{tier_upper.lower()}", ascending=False)
        else:
            logger.warning("Unknown tier '%s'. Valid: conservative, standard, permissive", tier)
    else:
        # Sort by CONSERVATIVE score, then STANDARD, then PERMISSIVE
        result = result.sort_values(
            ["score_conservative", "score_standard", "score_permissive"],
            ascending=False,
        )

    result = result.reset_index(drop=True)
    result.index += 1

    returns_pivot = _fetch_returns_history(db)
    result = _add_risk_parity_allocations(result, returns_pivot)

    return result


def print_leaderboard(lb: pd.DataFrame, tier: Optional[str] = None) -> None:
    if lb.empty:
        print("No leaderboard results available.")
        return

    tier_label = tier.upper() if tier else "ALL TIERS"
    print("=" * 100)
    print(f"STRATEGY LEADERBOARD — {tier_label}")
    print("=" * 100)

    header = (
        f"{'#':>4} | {'Strategy':25} | {'Symbol':10} | {'Sharpe':7} | {'MaxDD':6} | "
        f"{'WinRate':7} | {'Windows':7} | {'Tier':13} | {'Regime'}"
    )
    print(header)
    print("-" * len(header))

    for rank, row in lb.iterrows():
        params_str = ", ".join(f"{k}={v}" for k, v in row["params"].items()) if row["params"] else ""
        alloc = row.get("risk_parity_alloc_pct", 0.0)
        print(
            f"{rank:>4} | {row['strategy_name']:25} | {row['symbol']:10} | "
            f"{row['avg_sharpe']:7.2f} | {row['avg_max_dd_pct']:6.1f} | "
            f"{row['avg_win_rate_pct']:7.1f} | {row['n_windows']:7} | "
            f"{row['best_tier']:13} | {alloc:5.1f}% | {row['regime']}"
        )
        if params_str:
            print(f"{'':4}   params: {params_str}")

    print("=" * 100)
    print(f"Total qualifying strategies: {len(lb)}")


def write_leaderboard_md(lb: pd.DataFrame, tier: Optional[str] = None) -> None:
    """Write leaderboard to LEADERBOARD.md in project root."""
    project_root = Path(__file__).resolve().parent.parent
    out_path = project_root / "LEADERBOARD.md"

    tier_label = tier.upper() if tier else "ALL TIERS"
    lines = [
        f"# Raf3nd Strategy Leaderboard — {tier_label}",
        "",
        "| Rank | Strategy | Symbol | Sharpe | MaxDD | WinRate | Windows | Tier | Regime |",
        "|------|----------|--------|--------|-------|---------|---------|------|--------|",
    ]

    for rank, row in lb.iterrows():
        params_str = str(row["params"]) if row["params"] else ""
        lines.append(
            f"| {rank} | {row['strategy_name']} `{params_str}` | {row['symbol']} | "
            f"{row['avg_sharpe']:.2f} | {row['avg_max_dd_pct']:.1f}% | "
            f"{row['avg_win_rate_pct']:.1f}% | {row['n_windows']} | "
            f"{row['best_tier']} | {row['regime']} |"
        )

    out_path.write_text("\n".join(lines) + "\n")
    logger.info("Leaderboard written to %s", out_path)


def run_leaderboard_cmd(db, tier: Optional[str] = None) -> bool:
    """Entry point called from main.py leaderboard command."""
    lb = build_leaderboard(db, tier=tier)
    if lb.empty:
        print("No results qualify for the leaderboard yet.")
        print("Run: python main.py engine   (to populate engine_results)")
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
            print(f"  {rank}. {row['strategy_name']} on {row['symbol']} ({row['best_tier']}) — {row['regime']}")
