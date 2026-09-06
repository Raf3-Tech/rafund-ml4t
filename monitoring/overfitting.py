"""Leaderboard overfitting correction — Phase 6 of the Kraken Prop risk gate.

The leaderboard's score (Sharpe x win_rate x consistency) selects across
every strategy x parameter-set x symbol combination searched — the winner
is partly the luckiest of however many trials were run. This module adds
two independent checks, using ONLY data engine_results already has (no new
backtest runs, no new raw-return-series plumbing):

1. Deflated Sharpe Ratio (Bailey & Lopez de Prado, 2014) — the probability
   that a candidate's Sharpe reflects real skill rather than the best of
   N noisy trials. sigma(SR) across trials (the expected-max-under-the-null
   term needs the cross-sectional spread of Sharpe ratios across every
   trial actually searched) comes directly from the avg_sharpe values
   already computed for every candidate on this leaderboard build — zero
   new data required. sigma(SR_hat) (the candidate's OWN Sharpe estimation
   error) uses the standard closed-form assuming Gaussian returns (skew=0,
   kurtosis=3) — a DOCUMENTED SIMPLIFICATION: engine_results stores
   per-window summary stats, not the raw per-trade return series the exact
   formula needs for real skew/kurtosis. This is directional, not exact — a
   genuinely skewed/fat-tailed strategy's true DSR could differ from what
   this reports.

2. Walk-forward out-of-sample check — chronologically split a candidate's
   own per-window Sharpe values in half; the second half's mean Sharpe must
   fall inside a confidence band built from the first half's mean and
   standard error. Needs at least 4 windows (2 per half) to mean anything;
   fewer returns None (insufficient evidence — treated as NOT passing, per
   this brief's "cannot breach" philosophy, not as passing by default).

No scipy dependency: the inverse-normal quantile function uses Acklam's
rational approximation (~1e-9 accurate), and the normal CDF uses math.erf.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

# DSR > 0.95 is the conventional "statistically significant" bar in the
# Bailey/Lopez de Prado literature — a widely-used convention, not a
# hardcoded spec fact; config-overridable.
DEFAULT_MIN_DEFLATED_SHARPE = 0.95
# z-multiplier for the walk-forward OOS confidence band (1.96 ~= 95%).
DEFAULT_OOS_CONFIDENCE_Z = 1.96

_EULER_MASCHERONI = 0.5772156649015329


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Inverse normal CDF via Acklam's rational approximation."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    p_low = 0.02425
    p_high = 1 - p_low
    if p < p_low:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p <= p_high:
        q = p - 0.5
        r = q * q
        return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
               (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
    q = math.sqrt(-2 * math.log(1 - p))
    return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
             ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)


def sharpe_standard_error(sharpe_hat: float, n_obs: int, skew: float = 0.0, kurtosis: float = 3.0) -> Optional[float]:
    """Closed-form standard error of an estimated Sharpe ratio (Lo, 2002;
    Bailey & Lopez de Prado, 2014). skew=0/kurtosis=3 (Gaussian) unless the
    caller has the raw return series to compute the real values."""
    if n_obs <= 1:
        return None
    variance = (1 - skew * sharpe_hat + ((kurtosis - 1) / 4) * sharpe_hat ** 2) / (n_obs - 1)
    if variance < 0:
        return None
    return math.sqrt(variance)


def expected_max_sharpe_under_null(trial_sharpes: Sequence[float], n_trials: int) -> float:
    """SR_0 — the expected maximum Sharpe from n_trials trials of pure
    noise, given the observed cross-sectional spread of Sharpe ratios
    across those trials."""
    if n_trials <= 1 or len(trial_sharpes) <= 1:
        return 0.0
    sigma_sr = float(np.std(trial_sharpes, ddof=1))
    if sigma_sr <= 0:
        return 0.0
    term1 = (1 - _EULER_MASCHERONI) * _norm_ppf(1 - 1.0 / n_trials)
    term2 = _EULER_MASCHERONI * _norm_ppf(1 - 1.0 / (n_trials * math.e))
    return sigma_sr * (term1 + term2)


@dataclass(frozen=True)
class DeflatedSharpeResult:
    """Everything deflated_sharpe_ratio computed on the way to `probability`.

    `probability` at 0.0 (or 1.0) is a legitimate, honest answer — the
    normal CDF genuinely saturates to the double-precision floor/ceiling for
    an extreme enough z_score, well before the true (infinitesimally small)
    probability would print as anything else. `underflowed` says whether
    that saturation happened, so a 0.0 from "clearly not skill" and a 0.0
    from "the CDF floored" are distinguishable from the outside — z_score
    itself stays signed and unclamped either way, so ranking and
    distance-from-threshold survive even when every probability floors to
    the same value.
    """
    probability: float
    z_score: float
    observed_sharpe: float
    expected_max_sharpe_null: float
    n_trials: int
    n_observations: int
    skew: float
    kurtosis: float
    underflowed: bool


def deflated_sharpe_ratio(
    sharpe_hat: float, n_windows: int, trial_sharpes: Sequence[float], n_trials: int,
    skew: float = 0.0, kurtosis: float = 3.0,
) -> Optional[DeflatedSharpeResult]:
    """`probability` (0-1) is the chance sharpe_hat reflects real skill, not
    the best of n_trials noisy trials — plus the full diagnostic trail
    behind it (see DeflatedSharpeResult). None only when there isn't enough
    data to compute a standard error at all (fewer than 2 windows for this
    candidate) — that's a missing-data case, not a 0.0 verdict."""
    se = sharpe_standard_error(sharpe_hat, n_windows, skew=skew, kurtosis=kurtosis)
    if se is None or se <= 0:
        return None
    sr_0 = expected_max_sharpe_under_null(trial_sharpes, n_trials)
    z = (sharpe_hat - sr_0) / se
    probability = _norm_cdf(z)
    return DeflatedSharpeResult(
        probability=probability,
        z_score=z,
        observed_sharpe=sharpe_hat,
        expected_max_sharpe_null=sr_0,
        n_trials=n_trials,
        n_observations=n_windows,
        skew=skew,
        kurtosis=kurtosis,
        underflowed=(probability == 0.0),
    )


def walk_forward_oos_within_band(
    window_sharpes_by_time: Sequence[float], z: float = DEFAULT_OOS_CONFIDENCE_Z,
) -> Optional[bool]:
    """window_sharpes_by_time must already be ordered by window_end.
    Chronologically splits in half; True if the second half's mean Sharpe
    falls within a z-sigma confidence band built from the first half's mean
    and standard error. None if fewer than 4 windows (2 per half isn't
    enough to say anything meaningful)."""
    n = len(window_sharpes_by_time)
    if n < 4:
        return None
    mid = n // 2
    in_sample = window_sharpes_by_time[:mid]
    out_of_sample = window_sharpes_by_time[mid:]
    is_mean = float(np.mean(in_sample))
    is_se = sharpe_standard_error(is_mean, len(in_sample))
    if is_se is None:
        return None
    oos_mean = float(np.mean(out_of_sample))
    lower, upper = is_mean - z * is_se, is_mean + z * is_se
    return lower <= oos_mean <= upper
