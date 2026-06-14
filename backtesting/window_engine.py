"""Walk-forward window engine for Raf3nd.

Generates expanding and rolling time windows for every strategy-symbol
combination, runs each strategy, stores every result (pass or fail) to
engine_results, and triggers parameter mutation when a result fails.
"""

from __future__ import annotations

import itertools
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from backtesting.metrics import compute_performance_metrics, empty_metrics
from config.constants import PERIODS_PER_YEAR

logger = logging.getLogger(__name__)

# ── Regime tier thresholds ────────────────────────────────────────────────────
CONSERVATIVE_MAX_DD = 3.0
STANDARD_MAX_DD = 10.0
PERMISSIVE_MAX_DD = 25.0

# Minimum cumulative return for CONSERVATIVE pass — must also meet the 9% profit
# target of the 1-phase prop challenge to count as a challenge-worthy window.
CONSERVATIVE_MIN_RETURN = 9.0

MIN_TRADEABLE_BARS = 30
MAX_MUTATION_GENERATIONS = 3

# Funding rates settle every 8h → 3 observations/day → cadence-aware annualization.
FUNDING_PERIODS_PER_YEAR = PERIODS_PER_YEAR * 3

# ── Promotion gates (Rule 7) ──────────────────────────────────────────────────
PROMOTION_GATES = {
    "min_windows_positive_sharpe": 3,   # out of 5
    "min_windows_pass_dd": 4,           # out of 5
    "min_trades_per_window": 10,
    "min_win_rate_pct": 40.0,
}


@dataclass
class WindowSpec:
    window_type: str       # "EXPANDING" or "ROLLING"
    start: date
    end: date
    years: float


@dataclass
class RunResult:
    run_id: str
    strategy_name: str
    symbol: str
    window_type: str
    window_start: date
    window_end: date
    window_years: float
    params: Dict
    total_return_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    win_rate_pct: float
    num_trades: int
    conservative_pass: bool
    standard_pass: bool
    permissive_pass: bool
    failure_reason: Optional[str]
    mutation_generation: int
    parent_run_id: Optional[str]
    bars_used: int
    regime_trend: float
    regime_volatility: float
    regime_direction: str


# ── Window generation ─────────────────────────────────────────────────────────

def generate_windows(genesis: date, today: date) -> List[WindowSpec]:
    """Produce expanding + rolling windows dynamically from genesis → today."""
    windows: List[WindowSpec] = []
    total_years = (today - genesis).days / 365.25

    # Expanding windows from genesis (2y, 3y, 4y, 5y, 6y, full)
    for years in [2, 3, 4, 5, 6]:
        end = genesis + timedelta(days=int(years * 365.25))
        if end > today:
            break
        windows.append(WindowSpec(
            window_type="EXPANDING",
            start=genesis,
            end=end,
            years=years,
        ))
    if total_years >= 1.0:
        windows.append(WindowSpec(
            window_type="EXPANDING",
            start=genesis,
            end=today,
            years=round((today - genesis).days / 365.25, 2),
        ))

    # Rolling 2-year windows stepping 6 months
    _add_rolling(windows, genesis, today, window_years=2)
    # Rolling 3-year windows stepping 6 months
    _add_rolling(windows, genesis, today, window_years=3)

    # Deduplicate by (start, end)
    seen = set()
    unique = []
    for w in windows:
        key = (w.start, w.end)
        if key not in seen:
            seen.add(key)
            unique.append(w)

    return sorted(unique, key=lambda w: (w.start, w.end))


def _add_rolling(
    windows: List[WindowSpec],
    genesis: date,
    today: date,
    window_years: int,
    step_months: int = 6,
) -> None:
    step_days = int(step_months * 30.44)
    window_days = int(window_years * 365.25)
    start = genesis
    while True:
        end = start + timedelta(days=window_days)
        if end > today:
            break
        windows.append(WindowSpec(
            window_type="ROLLING",
            start=start,
            end=end,
            years=window_years,
        ))
        start = start + timedelta(days=step_days)


# ── Regime computation ────────────────────────────────────────────────────────

def compute_regime(df: pd.DataFrame) -> Tuple[float, float, str]:
    """Return (regime_trend, regime_volatility, regime_direction) for a price window."""
    close = df["close"].astype(float).reset_index(drop=True)
    if len(close) < 5:
        return 0.0, 0.0, "neutral"

    # Trend strength: R² of a linear regression. Use log(close) for strictly
    # positive price series; fall back to the raw series for funding rates
    # (which cross zero / go negative, where log is undefined).
    series = close.dropna()
    if (series > 0).all():
        series = np.log(series)
    if len(series) < 5:
        r_squared = 0.0
    else:
        x = np.arange(len(series))
        coeffs = np.polyfit(x, series, 1)
        fitted = np.polyval(coeffs, x)
        ss_res = np.sum((series - fitted) ** 2)
        ss_tot = np.sum((series - series.mean()) ** 2)
        r_squared = float(1 - ss_res / ss_tot) if ss_tot > 0 else 0.0

    # Volatility: average daily ATR as % of price. Funding-rate frames carry no
    # high/low, so fall back to close (ATR ≈ 0) instead of raising.
    high = df["high"].astype(float).reset_index(drop=True) if "high" in df.columns else close
    low = df["low"].astype(float).reset_index(drop=True) if "low" in df.columns else close
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr_pct = float((tr / close).mean() * 100) if not close.isna().all() else 0.0

    # Direction: overall total return positive or negative
    first_valid = close.dropna().iloc[0] if not close.dropna().empty else 1.0
    last_valid = close.dropna().iloc[-1] if not close.dropna().empty else 1.0
    direction = "bull" if last_valid >= first_valid else "bear"

    return round(r_squared, 4), round(atr_pct, 4), direction


# ── Backtest metrics from signals ─────────────────────────────────────────────

def _stats_from_equity(
    equity_curve: List[float],
    num_wins: int,
    num_trades: int,
    annualization: int,
) -> Dict:
    """Thin alias — delegates to the shared backtesting.metrics module (Phase D)."""
    return compute_performance_metrics(equity_curve, num_wins, num_trades, annualization)


def _compute_metrics_from_signals(
    df: pd.DataFrame,
    signals: pd.Series,
    commission: float = 0.001,
    annualization: int = PERIODS_PER_YEAR,
) -> Dict:
    """Mark-to-market backtest of a BUY/SELL/HOLD series (one-unit position).

    Position semantics (Rule-1 consistent with the strategy descriptions):
    BUY/SELL set the target position; **HOLD carries the current position** (it
    does NOT flatten). A position is held until the opposite signal, so a trend
    entry is held across the HOLD bars between crossovers and DCA accumulates.
    The open position is marked to market every bar, so Sharpe and drawdown
    reflect intra-hold moves; commission is charged on turnover at each change.
    """
    # Guard against missing candles: a NaN close would NaN-poison the equity curve.
    close = df["close"].astype(float).ffill().bfill().reset_index(drop=True)
    sig = signals.reset_index(drop=True)
    if len(close) < 2:
        return _empty_metrics()

    position = 0      # +1 long, -1 short, 0 flat
    equity = 1.0
    equity_curve = [equity]
    entry_equity = equity
    num_wins = 0
    num_trades = 0

    for i in range(1, len(close)):
        curr_sig = sig.iloc[i]
        if curr_sig == "BUY":
            target = 1
        elif curr_sig == "SELL":
            target = -1
        else:
            target = position  # HOLD carries the current position

        # Mark the position held over [i-1, i] to market.
        prev_px = close.iloc[i - 1]
        if position != 0 and prev_px > 0:
            equity *= 1.0 + (close.iloc[i] / prev_px - 1.0) * position

        if target != position:
            turnover = abs(target - position)          # 1 to open/close, 2 to flip
            equity *= 1.0 - commission * turnover
            if position != 0:                          # realize the closed trade
                num_trades += 1
                if equity > entry_equity:
                    num_wins += 1
            if target != 0:
                entry_equity = equity
            position = target

        equity_curve.append(equity)

    # Realize any position still open at the window end (for win-rate/num_trades).
    if position != 0:
        num_trades += 1
        if equity > entry_equity:
            num_wins += 1

    return _stats_from_equity(equity_curve, num_wins, num_trades, annualization)


def _compute_metrics_from_funding(
    rates: pd.Series,
    signals: pd.Series,
    commission: float = 0.001,
    annualization: int = FUNDING_PERIODS_PER_YEAR,
) -> Dict:
    """Mark-to-market a funding-rate strategy.

    Funding P&L is *additive income*, not a price return: while holding a
    position you receive ``position * funding_rate`` each 8h period (BUY = short
    perp / long spot collects positive funding; SELL collects negative funding).
    Treating the rate series as a price (rate[i]/rate[i-1]) is wrong — it
    explodes through zero — so this path is distinct from the price metrics.
    """
    r = rates.astype(float).fillna(0.0).reset_index(drop=True)
    sig = signals.reset_index(drop=True)
    if len(r) < 2:
        return _empty_metrics()

    position = 0
    equity = 1.0
    equity_curve = [equity]
    entry_equity = equity
    num_wins = 0
    num_trades = 0

    for i in range(1, len(r)):
        curr_sig = sig.iloc[i]
        if curr_sig == "BUY":
            target = 1
        elif curr_sig == "SELL":
            target = -1
        else:
            target = position  # carry

        # Income earned over this period from the position held into it.
        equity *= 1.0 + position * r.iloc[i]

        if target != position:
            equity *= 1.0 - commission * abs(target - position)
            if position != 0:
                num_trades += 1
                if equity > entry_equity:
                    num_wins += 1
            if target != 0:
                entry_equity = equity
            position = target

        equity_curve.append(equity)

    if position != 0:
        num_trades += 1
        if equity > entry_equity:
            num_wins += 1

    return _stats_from_equity(equity_curve, num_wins, num_trades, annualization)


def _compute_metrics_from_positions(
    price_a: pd.Series,
    price_b: pd.Series,
    pos_a: pd.Series,
    pos_b: pd.Series,
    commission: float = 0.001,
    annualization: int = PERIODS_PER_YEAR,
) -> Dict:
    """Mark-to-market a pairs position series (already carried per bar).

    Daily return is the per-leg-weighted sum of both legs' returns; commission is
    charged on the gross turnover whenever either leg's position changes.
    """
    a = price_a.astype(float).ffill().bfill().reset_index(drop=True)
    b = price_b.astype(float).ffill().bfill().reset_index(drop=True)
    pa = pos_a.reset_index(drop=True)
    pb = pos_b.reset_index(drop=True)
    n = min(len(a), len(b), len(pa), len(pb))
    if n < 2:
        return _empty_metrics()

    equity = 1.0
    equity_curve = [equity]
    entry_equity = equity
    in_trade = False
    num_wins = 0
    num_trades = 0

    for i in range(1, n):
        held_a, held_b = pa.iloc[i - 1], pb.iloc[i - 1]
        gross = abs(held_a) + abs(held_b)
        if gross > 0 and a.iloc[i - 1] > 0 and b.iloc[i - 1] > 0:
            ra = a.iloc[i] / a.iloc[i - 1] - 1.0
            rb = b.iloc[i] / b.iloc[i - 1] - 1.0
            equity *= 1.0 + (held_a * ra + held_b * rb) / gross

        changed = (pa.iloc[i] != held_a) or (pb.iloc[i] != held_b)
        if changed:
            turnover = abs(pa.iloc[i] - held_a) + abs(pb.iloc[i] - held_b)
            equity *= 1.0 - commission * turnover
            if in_trade:                               # closing/repositioning a trade
                num_trades += 1
                if equity > entry_equity:
                    num_wins += 1
            now_flat = (pa.iloc[i] == 0 and pb.iloc[i] == 0)
            if not now_flat:
                entry_equity = equity
                in_trade = True
            else:
                in_trade = False

        equity_curve.append(equity)

    if in_trade:
        num_trades += 1
        if equity > entry_equity:
            num_wins += 1

    return _stats_from_equity(equity_curve, num_wins, num_trades, annualization)


def _empty_metrics() -> Dict:
    return empty_metrics()


# ── Pass/fail classification ──────────────────────────────────────────────────

def classify_passes(metrics: Dict) -> Tuple[bool, bool, bool]:
    dd = metrics["max_drawdown_pct"]
    sharpe = metrics["sharpe_ratio"]
    wr = metrics["win_rate_pct"]
    total_ret = metrics.get("total_return_pct", 0.0)
    # CONSERVATIVE requires the 9% profit target AND the 3% DD ceiling — this
    # maps directly to the 1-phase prop challenge. A window that is profitable
    # but doesn't reach 9% would not fund the challenge, so it doesn't qualify.
    conservative = (
        dd <= CONSERVATIVE_MAX_DD
        and total_ret >= CONSERVATIVE_MIN_RETURN
        and sharpe > 0
        and wr > 40.0
    )
    standard = dd <= STANDARD_MAX_DD and sharpe > 0 and wr > 40.0
    permissive = dd <= PERMISSIVE_MAX_DD and sharpe > 0
    return conservative, standard, permissive


# ── Parameter grid expansion ──────────────────────────────────────────────────

def expand_param_grid(param_grid: Dict) -> List[Dict]:
    """Produce all combinations from a grid of lists, or wrap scalars as singletons."""
    keys = list(param_grid.keys())
    value_lists = []
    for k in keys:
        v = param_grid[k]
        value_lists.append(v if isinstance(v, list) else [v])
    return [dict(zip(keys, combo)) for combo in itertools.product(*value_lists)]


def _mutation_grid(strategy) -> List[Dict]:
    """Build full param combinations from strategy mutation candidates."""
    raw = {}
    for k, v in strategy.param_grid.items():
        raw[k] = v if isinstance(v, list) else [v]
    # Check for _*_GRID class attributes (e.g. _FAST_GRID, _SLOW_GRID)
    for attr in dir(strategy):
        if attr.startswith("_") and attr.endswith("_GRID"):
            key = attr[1:-5].lower()  # "_FAST_GRID" → "fast"
            if key in raw:
                raw[key] = getattr(strategy, attr)
    return expand_param_grid(raw)


# ── Promotion gate (PROMOTION_GATES) ─────────────────────────────────────────


def check_promotion_gate(results: List[RunResult]) -> Dict[str, bool]:
    """Check PROMOTION_GATES for each (strategy_name, symbol) combination.

    Evaluates the last 5 windows per combo against all four gates:
      - min_windows_positive_sharpe: Sharpe > 0 in >= 3 of last 5 windows
      - min_windows_pass_dd:         permissive_pass in >= 4 of last 5 windows
      - min_trades_per_window:       every window in the last 5 has >= 10 trades
      - min_win_rate_pct:            average win rate over last 5 >= 40%

    Returns:
        Dict mapping "strategy_name|symbol" → True if all gates pass.
    """
    from collections import defaultdict
    groups: Dict[str, List[RunResult]] = defaultdict(list)
    for r in results:
        groups[f"{r.strategy_name}|{r.symbol}"].append(r)

    eligibility: Dict[str, bool] = {}
    for key, group in groups.items():
        recent = sorted(group, key=lambda r: r.window_end)[-5:]
        if not recent:
            eligibility[key] = False
            continue

        n_pos_sharpe = sum(1 for r in recent if r.sharpe_ratio > 0)
        n_pass_dd = sum(1 for r in recent if r.permissive_pass)
        avg_wr = sum(r.win_rate_pct for r in recent) / len(recent)
        min_trades_ok = all(
            r.num_trades >= PROMOTION_GATES["min_trades_per_window"] for r in recent
        )

        eligible = (
            n_pos_sharpe >= PROMOTION_GATES["min_windows_positive_sharpe"]
            and n_pass_dd >= PROMOTION_GATES["min_windows_pass_dd"]
            and min_trades_ok
            and avg_wr >= PROMOTION_GATES["min_win_rate_pct"]
        )
        eligibility[key] = eligible

        level = logger.info if eligible else logger.debug
        level(
            "PROMOTION GATE %s | %s | sharpe_windows=%d/%d pass_dd_windows=%d/%d "
            "avg_wr=%.1f%% min_trades=%s → %s",
            key, "ELIGIBLE" if eligible else "NOT ELIGIBLE",
            n_pos_sharpe, len(recent), n_pass_dd, len(recent),
            avg_wr, min_trades_ok, eligible,
        )

    return eligibility


# ── Main engine ───────────────────────────────────────────────────────────────

class WalkForwardWindowEngine:
    """Run all strategies on all symbols across all time windows."""

    def __init__(
        self,
        db,
        strategies: Optional[List] = None,
        symbols: Optional[List[str]] = None,
        commission: float = 0.001,
    ):
        self.db = db
        self.strategies = strategies or []
        self.symbols = symbols or []
        self.commission = commission
        self.run_id = str(uuid.uuid4())

    def run(
        self,
        strategy_filter: Optional[str] = None,
        symbol_filter: Optional[str] = None,
    ) -> List[RunResult]:
        logger.info("=" * 80)
        logger.info("WALK-FORWARD ENGINE RUN — run_id=%s", self.run_id)
        logger.info("=" * 80)

        all_results: List[RunResult] = []

        strategies = self.strategies
        if strategy_filter:
            strategies = [s for s in strategies if strategy_filter.lower() in s.name.lower()]

        symbols = self.symbols
        if symbol_filter:
            symbols = [s for s in symbols if s == symbol_filter]

        from strategies.base import BasePairsStrategy
        for strategy in strategies:
            if getattr(strategy, "timeframe", None) == "8h":
                # Funding strategies run on their own 8h cadence, not daily bars.
                all_results.extend(self._run_funding_strategy(strategy, symbol_filter))
            elif isinstance(strategy, BasePairsStrategy):
                all_results.extend(self._run_pairs_strategy(strategy, symbol_filter))
            else:
                for symbol in symbols:
                    all_results.extend(self._run_strategy_symbol(strategy, symbol))

        self._persist_results(all_results)
        logger.info("Engine run complete — %d results written", len(all_results))

        if all_results:
            gate_results = check_promotion_gate(all_results)
            eligible = [k for k, v in gate_results.items() if v]
            logger.info(
                "Promotion gate: %d / %d combos eligible — %s",
                len(eligible), len(gate_results),
                eligible or "none",
            )

        return all_results

    def _run_strategy_symbol(self, strategy, symbol: str) -> List[RunResult]:
        """Run one single-asset strategy on one symbol across all eligible windows."""
        prices = self._load_prices(symbol)
        if prices is None or len(prices) < 2:
            logger.warning("[SKIP] %s: no price data", symbol)
            return []
        return self._run_over_windows(strategy, symbol, prices, PERIODS_PER_YEAR)

    def _run_over_windows(
        self, strategy, symbol: str, frame: pd.DataFrame, annualization: int,
        funding_mode: bool = False,
    ) -> List[RunResult]:
        """Shared expanding+rolling window loop for any single-series strategy
        (daily price OR 8h funding). ``frame`` must carry ``timestamp`` + ``close``.
        ``funding_mode`` selects additive-income P&L instead of price-return P&L."""
        genesis = frame["timestamp"].min().date()
        today = frame["timestamp"].max().date()
        windows = generate_windows(genesis, today)

        default_params = {k: (v[0] if isinstance(v, list) else v) for k, v in strategy.param_grid.items()}
        min_bars = strategy.get_min_bars(default_params)
        min_history = min_bars + 365

        total_days = (today - genesis).days
        if total_days < min_history:
            logger.warning(
                "[EXCLUDE] %s on %s: only %d days history, need %d",
                strategy.name, symbol, total_days, min_history,
            )
            return []

        results: List[RunResult] = []
        tried_params: dict = {}  # key = (window_start, window_end) → set of param json

        for window in windows:
            window_key = (window.start, window.end)
            tried_params.setdefault(window_key, set())

            default_result = self._run_window(
                strategy, symbol, frame, window,
                params=default_params, mutation_generation=0, parent_run_id=None,
                annualization=annualization, funding_mode=funding_mode,
            )
            if default_result is None:
                continue

            results.append(default_result)
            tried_params[window_key].add(json.dumps(default_params, sort_keys=True))

            # Mutate if failed even PERMISSIVE
            if not default_result.permissive_pass:
                mutation_results = self._mutate(
                    strategy, symbol, frame, window,
                    parent_result=default_result,
                    tried_params=tried_params[window_key],
                    annualization=annualization, funding_mode=funding_mode,
                )
                results.extend(mutation_results)
                tried_params[window_key].update(
                    json.dumps(r.params, sort_keys=True) for r in mutation_results
                )

        return results

    def _run_window(
        self,
        strategy,
        symbol: str,
        prices: pd.DataFrame,
        window: WindowSpec,
        params: Dict,
        mutation_generation: int,
        parent_run_id: Optional[str],
        annualization: int = PERIODS_PER_YEAR,
        funding_mode: bool = False,
    ) -> Optional[RunResult]:
        """Run strategy on a single window. Returns None if window is ineligible."""
        min_bars = strategy.get_min_bars(params)

        mask = (
            (prices["timestamp"].dt.date >= window.start) &
            (prices["timestamp"].dt.date <= window.end)
        )
        window_df = prices[mask].copy().reset_index(drop=True)

        if len(window_df) < min_bars + MIN_TRADEABLE_BARS:
            logger.debug(
                "[SKIP] %s / %s / %s→%s: %d bars < min_bars+30=%d",
                strategy.name, symbol, window.start, window.end,
                len(window_df), min_bars + MIN_TRADEABLE_BARS,
            )
            return None

        regime_trend, regime_vol, regime_dir = compute_regime(window_df)

        try:
            signals = strategy.generate_signals(window_df, params)
        except Exception as e:
            logger.error(
                "Signal generation error %s / %s: %s", strategy.name, symbol, e, exc_info=True
            )
            return None

        # Discard warmup rows
        active_df = window_df.iloc[min_bars:].copy().reset_index(drop=True)
        active_signals = signals.iloc[min_bars:].reset_index(drop=True)
        bars_used = len(active_df)

        if funding_mode:
            metrics = _compute_metrics_from_funding(
                active_df["close"], active_signals, self.commission, annualization=annualization
            )
        else:
            metrics = _compute_metrics_from_signals(
                active_df, active_signals, self.commission, annualization=annualization
            )
        conservative, standard, permissive = classify_passes(metrics)

        failure_reason = None
        if not permissive:
            failure_reason = strategy.describe_failure(metrics)

        level = logging.INFO if standard else logging.WARNING
        logger.log(
            level,
            "%s / %s / %s→%s | gen=%d | sharpe=%.2f | dd=%.1f%% | trades=%d | cons=%s std=%s perm=%s",
            strategy.name, symbol, window.start, window.end, mutation_generation,
            metrics["sharpe_ratio"], metrics["max_drawdown_pct"], metrics["num_trades"],
            conservative, standard, permissive,
        )

        return RunResult(
            run_id=self.run_id,
            strategy_name=strategy.name,
            symbol=symbol,
            window_type=window.window_type,
            window_start=window.start,
            window_end=window.end,
            window_years=window.years,
            params=params,
            total_return_pct=metrics["total_return_pct"],
            sharpe_ratio=metrics["sharpe_ratio"],
            max_drawdown_pct=metrics["max_drawdown_pct"],
            win_rate_pct=metrics["win_rate_pct"],
            num_trades=metrics["num_trades"],
            conservative_pass=conservative,
            standard_pass=standard,
            permissive_pass=permissive,
            failure_reason=failure_reason,
            mutation_generation=mutation_generation,
            parent_run_id=parent_run_id,
            bars_used=bars_used,
            regime_trend=regime_trend,
            regime_volatility=regime_vol,
            regime_direction=regime_dir,
        )

    def _mutate(
        self,
        strategy,
        symbol: str,
        prices: pd.DataFrame,
        window: WindowSpec,
        parent_result: RunResult,
        tried_params: set,
        annualization: int = PERIODS_PER_YEAR,
        funding_mode: bool = False,
    ) -> List[RunResult]:
        if parent_result.mutation_generation >= MAX_MUTATION_GENERATIONS:
            return []

        all_combos = _mutation_grid(strategy)
        mutation_results: List[RunResult] = []

        for params in all_combos:
            key = json.dumps(params, sort_keys=True)
            if key in tried_params:
                continue

            result = self._run_window(
                strategy, symbol, prices, window,
                params=params,
                mutation_generation=parent_result.mutation_generation + 1,
                parent_run_id=parent_result.run_id,
                annualization=annualization, funding_mode=funding_mode,
            )
            if result is not None:
                mutation_results.append(result)
                tried_params.add(key)
                if result.permissive_pass:
                    # Found a working combo — stop mutating this window
                    break

        return mutation_results

    # ── Funding-rate strategies (8h cadence) ────────────────────────────────────

    def _run_funding_strategy(self, strategy, symbol_filter: Optional[str]) -> List[RunResult]:
        """Run a funding-rate strategy on 8h funding data per perp symbol."""
        from data.collectors.binance_funding_collector import SYMBOL_MAP

        perp_symbols = [s for s in self.symbols if s in SYMBOL_MAP]
        if symbol_filter:
            perp_symbols = [s for s in perp_symbols if s == symbol_filter]
        if not perp_symbols:
            logger.warning("[SKIP] %s: no perp symbols with funding data", strategy.name)
            return []

        results: List[RunResult] = []
        for symbol in perp_symbols:
            try:
                frame = self.db.get_funding_rates(symbol)
            except Exception as e:
                logger.error("Failed to load funding rates for %s: %s", symbol, e)
                continue
            if frame is None or len(frame) < 2:
                logger.warning("[SKIP] %s: no funding data", symbol)
                continue
            frame = frame.sort_values("timestamp").reset_index(drop=True)
            results.extend(
                self._run_over_windows(
                    strategy, symbol, frame, FUNDING_PERIODS_PER_YEAR, funding_mode=True
                )
            )
        return results

    # ── Pairs strategies ────────────────────────────────────────────────────────

    def _run_pairs_strategy(self, strategy, symbol_filter: Optional[str]) -> List[RunResult]:
        """Run a pairs strategy across all valid symbol pairs and windows."""
        try:
            pairs = self.db.get_feature_pairs()
        except Exception as e:
            logger.error("Failed to load feature pairs: %s", e)
            return []
        if not pairs:
            logger.warning("[SKIP] %s: no feature pairs available", strategy.name)
            return []
        if symbol_filter:
            pairs = [(a, b) for (a, b) in pairs if symbol_filter in (a, b)]

        default_params = {k: (v[0] if isinstance(v, list) else v) for k, v in strategy.param_grid.items()}
        min_bars = strategy.get_min_bars(default_params)

        results: List[RunResult] = []
        for sym_a, sym_b in pairs:
            pa = self._load_prices(sym_a)
            pb = self._load_prices(sym_b)
            if pa is None or pb is None:
                continue

            left = pa[["timestamp", "close"]].rename(columns={"close": "close_a"})
            for col in ("high", "low"):
                if col in pa.columns:
                    left[f"{col}_a"] = pa[col].values
            right = pb[["timestamp", "close"]].rename(columns={"close": "close_b"})
            merged = (
                left.merge(right, on="timestamp", how="inner")
                .sort_values("timestamp").reset_index(drop=True)
            )
            if len(merged) < 2:
                continue

            label = f"{sym_a}|{sym_b}"
            genesis = merged["timestamp"].min().date()
            today = merged["timestamp"].max().date()
            if (today - genesis).days < min_bars + 365:
                logger.warning("[EXCLUDE] %s on %s: insufficient overlapping history", strategy.name, label)
                continue

            tried: dict = {}
            for window in generate_windows(genesis, today):
                wk = (window.start, window.end)
                tried.setdefault(wk, set())

                res = self._run_pair_window(strategy, label, merged, window, default_params, 0, None)
                if res is None:
                    continue
                results.append(res)
                tried[wk].add(json.dumps(default_params, sort_keys=True))

                if not res.permissive_pass:
                    for params in _mutation_grid(strategy):
                        key = json.dumps(params, sort_keys=True)
                        if key in tried[wk]:
                            continue
                        mr = self._run_pair_window(strategy, label, merged, window, params, 1, res.run_id)
                        if mr is not None:
                            results.append(mr)
                            tried[wk].add(key)
                            if mr.permissive_pass:
                                break
        return results

    def _run_pair_window(
        self, strategy, label: str, merged: pd.DataFrame, window: WindowSpec,
        params: Dict, mutation_generation: int, parent_run_id: Optional[str],
    ) -> Optional[RunResult]:
        min_bars = strategy.get_min_bars(params)
        mask = (
            (merged["timestamp"].dt.date >= window.start) &
            (merged["timestamp"].dt.date <= window.end)
        )
        wdf = merged[mask].copy().reset_index(drop=True)
        if len(wdf) < min_bars + MIN_TRADEABLE_BARS:
            return None

        regime_df = pd.DataFrame({"close": wdf["close_a"]})
        if "high_a" in wdf.columns:
            regime_df["high"] = wdf["high_a"].values
        if "low_a" in wdf.columns:
            regime_df["low"] = wdf["low_a"].values
        regime_trend, regime_vol, regime_dir = compute_regime(regime_df)

        df_a = pd.DataFrame({"timestamp": wdf["timestamp"], "close": wdf["close_a"]})
        df_b = pd.DataFrame({"timestamp": wdf["timestamp"], "close": wdf["close_b"]})
        try:
            pos = strategy.generate_signals_pair(df_a, df_b, params)
        except Exception as e:
            logger.error("Pair signal error %s / %s: %s", strategy.name, label, e, exc_info=True)
            return None

        active = wdf.iloc[min_bars:].reset_index(drop=True)
        pos_active = pos.iloc[min_bars:].reset_index(drop=True)
        bars_used = len(active)

        metrics = _compute_metrics_from_positions(
            active["close_a"], active["close_b"],
            pos_active["position_a"], pos_active["position_b"],
            self.commission, PERIODS_PER_YEAR,
        )
        conservative, standard, permissive = classify_passes(metrics)
        failure_reason = strategy.describe_failure(metrics) if not permissive else None

        level = logging.INFO if standard else logging.WARNING
        logger.log(
            level,
            "%s / %s / %s→%s | gen=%d | sharpe=%.2f | dd=%.1f%% | trades=%d | cons=%s std=%s perm=%s",
            strategy.name, label, window.start, window.end, mutation_generation,
            metrics["sharpe_ratio"], metrics["max_drawdown_pct"], metrics["num_trades"],
            conservative, standard, permissive,
        )

        return RunResult(
            run_id=self.run_id,
            strategy_name=strategy.name,
            symbol=label,
            window_type=window.window_type,
            window_start=window.start,
            window_end=window.end,
            window_years=window.years,
            params=params,
            total_return_pct=metrics["total_return_pct"],
            sharpe_ratio=metrics["sharpe_ratio"],
            max_drawdown_pct=metrics["max_drawdown_pct"],
            win_rate_pct=metrics["win_rate_pct"],
            num_trades=metrics["num_trades"],
            conservative_pass=conservative,
            standard_pass=standard,
            permissive_pass=permissive,
            failure_reason=failure_reason,
            mutation_generation=mutation_generation,
            parent_run_id=parent_run_id,
            bars_used=bars_used,
            regime_trend=regime_trend,
            regime_volatility=regime_vol,
            regime_direction=regime_dir,
        )

    def _load_prices(self, symbol: str) -> Optional[pd.DataFrame]:
        try:
            df = self.db.get_prices(symbol, None, None)
            if df is None or df.empty:
                return None
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            return df.sort_values("timestamp").reset_index(drop=True)
        except Exception as e:
            logger.error("Failed to load prices for %s: %s", symbol, e)
            return None

    def _persist_results(self, results: List[RunResult]) -> None:
        if not results:
            return
        rows = []
        for r in results:
            rows.append({
                "run_id": r.run_id,
                "strategy_name": r.strategy_name,
                "symbol": r.symbol,
                "window_type": r.window_type,
                "window_start": r.window_start.isoformat(),
                "window_end": r.window_end.isoformat(),
                "window_years": r.window_years,
                "params": json.dumps(r.params),
                "total_return_pct": r.total_return_pct,
                "sharpe_ratio": r.sharpe_ratio,
                "max_drawdown_pct": r.max_drawdown_pct,
                "win_rate_pct": r.win_rate_pct,
                "num_trades": r.num_trades,
                "conservative_pass": r.conservative_pass,
                "standard_pass": r.standard_pass,
                "permissive_pass": r.permissive_pass,
                "failure_reason": r.failure_reason,
                "mutation_generation": r.mutation_generation,
                "parent_run_id": r.parent_run_id,
                "bars_used": r.bars_used,
                "regime_trend": r.regime_trend,
                "regime_volatility": r.regime_volatility,
                "regime_direction": r.regime_direction,
            })
        try:
            self.db.insert_engine_results(rows)
        except Exception as e:
            logger.error("Failed to persist engine results: %s", e, exc_info=True)
