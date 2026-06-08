"""Genetic-algorithm optimization of triple-barrier parameters.

The GA searches the barrier geometry — take-profit multiple, stop-loss multiple,
holding horizon, and volatility-estimation span — to maximize the risk-adjusted
quality of the resulting labels.

Fitness (per the Phase-2 spec)::

    fitness = sharpe + profit_factor - max_drawdown

evaluated on the **non-overlapping** sequence of barrier-exit trades the labels
imply (enter long at each event, exit at the first barrier touched). Using
non-overlapping events removes the autocorrelation that overlapping horizons
would inject into the Sharpe/PF estimates.

Determinism: all randomness flows from a single seeded ``numpy`` generator, so a
given (prices, settings, seed) reproduces exactly — important for walk-forward
runs and for tests.

Walk-forward use: call :func:`optimize_barriers` on the **train** fold only, then
label the test fold with the returned config. The GA never sees test data.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import List, Optional

import numpy as np
import pandas as pd

from labels.triple_barrier import (
    BarrierConfig,
    drop_unusable_labels,
    ewma_volatility,
    triple_barrier_labels,
)

# Search-space bounds for each gene.
TP_BOUNDS = (0.5, 5.0)
SL_BOUNDS = (0.5, 5.0)
HORIZON_BOUNDS = (3, 40)
VOL_SPAN_BOUNDS = (10, 60)

_PF_CAP = 5.0          # clip profit factor so a single tiny loss can't dominate
_MIN_TRADES = 10       # configs yielding fewer non-overlapping trades are unfit


@dataclass
class FitnessReport:
    fitness: float
    sharpe: float
    profit_factor: float
    max_drawdown: float
    n_trades: int


def _non_overlapping_trade_returns(labels: pd.DataFrame) -> np.ndarray:
    """Walk events left→right, taking each usable event and skipping to its exit
    before taking the next, so the returned trades do not overlap in time."""
    clean = drop_unusable_labels(labels)
    if clean.empty:
        return np.array([])

    rets = clean["ret"].to_numpy(dtype=float)
    offsets = clean["exit_offset"].to_numpy(dtype=float)
    # Positional index of each usable event within the (cleaned) frame.
    positions = np.arange(len(clean))

    out: List[float] = []
    i = 0
    while i < len(clean):
        r = rets[i]
        off = offsets[i]
        if np.isfinite(r):
            out.append(r)
        # Skip past this trade's holding period to the next non-overlapping event.
        step = int(off) if np.isfinite(off) and off >= 1 else 1
        i += step
    return np.asarray(out, dtype=float)


def evaluate_fitness(
    prices: pd.Series,
    config: BarrierConfig,
    *,
    vol: Optional[pd.Series] = None,
) -> FitnessReport:
    """Score a single barrier config on ``prices`` (a train series)."""
    labels = triple_barrier_labels(prices, config, vol=vol)
    trades = _non_overlapping_trade_returns(labels)
    n = trades.size

    if n < _MIN_TRADES:
        # Too few trades to trust any statistic — make it strongly unfit but
        # rank by trade count so selection can climb toward viable regions.
        return FitnessReport(
            fitness=-10.0 + n * 0.01,
            sharpe=0.0,
            profit_factor=0.0,
            max_drawdown=1.0,
            n_trades=n,
        )

    mean = float(np.mean(trades))
    std = float(np.std(trades, ddof=1))
    sharpe = mean / std if std > 0 else 0.0

    gains = trades[trades > 0].sum()
    losses = -trades[trades < 0].sum()
    if losses <= 0:
        profit_factor = _PF_CAP if gains > 0 else 0.0
    else:
        profit_factor = min(gains / losses, _PF_CAP)

    equity = np.cumprod(1.0 + trades)
    peak = np.maximum.accumulate(equity)
    max_drawdown = float(np.max((peak - equity) / peak)) if equity.size else 1.0

    fitness = sharpe + profit_factor - max_drawdown
    return FitnessReport(
        fitness=float(fitness),
        sharpe=float(sharpe),
        profit_factor=float(profit_factor),
        max_drawdown=max_drawdown,
        n_trades=n,
    )


@dataclass
class GAResult:
    best_config: BarrierConfig
    best_report: FitnessReport
    history: List[float]  # best fitness per generation (for convergence checks)


def _random_config(rng: np.random.Generator) -> BarrierConfig:
    return BarrierConfig(
        tp_mult=float(rng.uniform(*TP_BOUNDS)),
        sl_mult=float(rng.uniform(*SL_BOUNDS)),
        horizon=int(rng.integers(HORIZON_BOUNDS[0], HORIZON_BOUNDS[1] + 1)),
        vol_span=int(rng.integers(VOL_SPAN_BOUNDS[0], VOL_SPAN_BOUNDS[1] + 1)),
    )


def _clip(value, lo, hi):
    return max(lo, min(hi, value))


def _crossover(a: BarrierConfig, b: BarrierConfig, rng: np.random.Generator) -> BarrierConfig:
    """Blend continuous genes; pick discrete genes from either parent."""
    w = rng.random()
    return BarrierConfig(
        tp_mult=w * a.tp_mult + (1 - w) * b.tp_mult,
        sl_mult=w * a.sl_mult + (1 - w) * b.sl_mult,
        horizon=a.horizon if rng.random() < 0.5 else b.horizon,
        vol_span=a.vol_span if rng.random() < 0.5 else b.vol_span,
    )


def _mutate(cfg: BarrierConfig, rng: np.random.Generator, rate: float) -> BarrierConfig:
    tp, sl, hz, vs = cfg.tp_mult, cfg.sl_mult, cfg.horizon, cfg.vol_span
    if rng.random() < rate:
        tp = _clip(tp + rng.normal(0, 0.5), *TP_BOUNDS)
    if rng.random() < rate:
        sl = _clip(sl + rng.normal(0, 0.5), *SL_BOUNDS)
    if rng.random() < rate:
        hz = int(_clip(hz + rng.integers(-4, 5), *HORIZON_BOUNDS))
    if rng.random() < rate:
        vs = int(_clip(vs + rng.integers(-6, 7), *VOL_SPAN_BOUNDS))
    return replace(cfg, tp_mult=tp, sl_mult=sl, horizon=hz, vol_span=vs)


def optimize_barriers(
    prices: pd.Series,
    *,
    population_size: int = 30,
    generations: int = 15,
    elite: int = 3,
    mutation_rate: float = 0.3,
    tournament_k: int = 3,
    seed: int = 0,
) -> GAResult:
    """Evolve a population of barrier configs to maximize fitness on ``prices``.

    Returns the best config found, its fitness report, and the per-generation
    best-fitness history (monotonically non-decreasing thanks to elitism, which
    makes convergence easy to assert).
    """
    rng = np.random.default_rng(seed)
    prices = pd.Series(prices, dtype="float64")

    population = [_random_config(rng) for _ in range(population_size)]
    best_config: Optional[BarrierConfig] = None
    best_report: Optional[FitnessReport] = None
    history: List[float] = []

    for _ in range(generations):
        reports = [evaluate_fitness(prices, cfg) for cfg in population]
        order = np.argsort([r.fitness for r in reports])[::-1]
        population = [population[i] for i in order]
        reports = [reports[i] for i in order]

        if best_report is None or reports[0].fitness > best_report.fitness:
            best_report = reports[0]
            best_config = population[0]
        history.append(best_report.fitness)

        # Elitism + tournament-selected, crossed-over, mutated offspring.
        next_pop: List[BarrierConfig] = list(population[:elite])
        while len(next_pop) < population_size:
            def pick() -> BarrierConfig:
                idx = rng.integers(0, population_size, size=tournament_k)
                champ = min(int(i) for i in idx)  # smaller rank index = fitter
                return population[champ]

            child = _crossover(pick(), pick(), rng)
            child = _mutate(child, rng, mutation_rate)
            next_pop.append(child.validate())
        population = next_pop

    assert best_config is not None and best_report is not None
    return GAResult(best_config=best_config, best_report=best_report, history=history)
