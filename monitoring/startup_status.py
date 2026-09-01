"""Print a structured system-state summary at process startup.

Called once by main.py at the top of any paper/live command so the operator
can confirm configuration, floor status, and leaderboard state before the
first trade cycle runs.
"""

from __future__ import annotations

from typing import Optional

from config.logging_config import get_logger

logger = get_logger(__name__)


def print_startup_status(db, exchange: Optional[str] = None) -> None:
    """Print system state to stdout (and logger.info).

    Covers:
    - Configured exchanges and active timeframes
    - Strategies currently in the paper cycle
    - Leaderboard top 3 (strategy, symbol, score, MaxDD, ready_for_live)
    - LOCKED / UNLOCKED state (any account_failed or manual_halt slots)
    - Eval mode ON / OFF, and which strategy is auto-selected if ON
    - Floor status: current cumulative DD and soft/hard floor thresholds
    """
    from config.loader import get_settings
    from monitoring.leaderboard import build_leaderboard, select_eval_strategy
    from trading.paper_trader import (
        SUPPORTED_EXCHANGES,
        _paper_candidates,
        _paper_run_id,
    )
    from trading.position import list_positions

    cfg = get_settings()

    exchanges = [exchange] if exchange else list(SUPPORTED_EXCHANGES)
    timeframes = getattr(cfg, "timeframes", ["1d"])
    eval_mode = getattr(cfg, "eval_mode", False)

    divider = "=" * 72
    print(divider)
    print("  ML4T STARTUP STATUS")
    print(divider)

    # ── Exchanges + timeframes ─────────────────────────────────────────────
    print(f"  Exchanges    : {', '.join(exchanges)}")
    print(f"  Timeframes   : {', '.join(timeframes)}")
    print(f"  Eval mode    : {'ON' if eval_mode else 'OFF'}")
    print(f"  Max trades/d : {cfg.max_trades_per_day}")
    print(f"  Risk/trade   : {cfg.risk_per_trade_pct * 100:.2f}%")
    print()

    # ── Candidate strategies ───────────────────────────────────────────────
    candidates = _paper_candidates(db)
    if candidates:
        print(f"  Paper strategies ({len(candidates)}):")
        for name, sym, _ in candidates:
            print(f"    - {name} / {sym}")
    else:
        print("  Paper strategies : none qualifying yet")
    print()

    # ── Eval auto-selection ────────────────────────────────────────────────
    if eval_mode:
        result = select_eval_strategy(db)
        if result:
            name, sym, _ = result
            print(f"  EVAL SELECTED    : {name} / {sym}")
        else:
            print("  EVAL SELECTED    : NONE — no ready_for_live strategy yet")
        print()

    # ── Leaderboard top 3 ─────────────────────────────────────────────────
    lb = build_leaderboard(db)
    print("  Leaderboard top 3:")
    if lb.empty:
        print("    (no engine_results yet)")
    else:
        for rank, row in lb.head(3).iterrows():
            rfl = " [LIVE-READY]" if row.get("ready_for_live") else ""
            print(
                f"    {rank}. {row['strategy_name']:25} / {row['symbol']:10} "
                f"score={row['score']:.4f}  MaxDD={row['avg_max_dd_pct']:.1f}%"
                f"  pass={row['pass_ratio']*100:.0f}%{rfl}"
            )
    print()

    # ── Floor status per slot ──────────────────────────────────────────────
    soft_floor_eq = getattr(cfg, "soft_floor_equity", cfg.account_size - 130.0)
    hard_floor_eq = getattr(cfg, "hard_floor_equity", cfg.account_size - 145.0)
    print(
        f"  Floor thresholds : soft=${cfg.account_size - soft_floor_eq:.0f} DD "
        f"(equity≤${soft_floor_eq:.0f})  "
        f"hard=${cfg.account_size - hard_floor_eq:.0f} DD "
        f"(equity≤${hard_floor_eq:.0f})"
    )
    print()

    any_locked = False
    for ex in exchanges:
        positions = list_positions(db, ex, run_id_prefix=f"paper_{ex}")
        for pos in positions:
            dd = cfg.account_size - pos.equity
            lock_reason = []
            if pos.account_failed:
                lock_reason.append("account_failed")
            if pos.manual_halt:
                lock_reason.append("manual_halt")
            if pos.daily_halt:
                lock_reason.append("daily_halt")
            lock_str = " | ".join(lock_reason) if lock_reason else "OK"
            if lock_reason:
                any_locked = True
            print(
                f"  [{ex}] {pos.strategy_name:25} / {pos.symbol:10} "
                f"equity=${pos.equity:,.2f}  DD=${dd:.2f}  {lock_str}"
            )

    if not any_locked:
        print("  Slot status      : UNLOCKED — all slots clear")
    else:
        print()
        print("  *** WARNING: one or more slots are LOCKED — review before trading ***")

    print(divider)
    print()
