"""
Risk management.

This module handles risk calculations, monitoring, and constraints.
"""

from __future__ import annotations

import pandas as pd
import numpy as np

from config.constants import PERIODS_PER_YEAR


class RiskManager:
    """Risk management and monitoring."""
    
    def __init__(self, var_confidence: float = 0.95):
        """
        Initialize risk manager.
        
        Args:
            var_confidence: Confidence level for Value-at-Risk calculation
        """
        self.var_confidence = var_confidence
        
    def calculate_var(self, returns: pd.Series) -> float:
        """
        Calculate Value-at-Risk.
        
        Args:
            returns: Series of returns
            
        Returns:
            VaR value
        """
        return np.percentile(returns, (1 - self.var_confidence) * 100)
    
    def calculate_cvar(self, returns: pd.Series) -> float:
        """
        Calculate Conditional Value-at-Risk (Expected Shortfall).
        
        Args:
            returns: Series of returns
            
        Returns:
            CVaR value
        """
        var = self.calculate_var(returns)
        return returns[returns <= var].mean()
    
    def calculate_max_drawdown(self, returns: pd.Series) -> float:
        """
        Calculate maximum drawdown.
        
        Args:
            returns: Series of returns
            
        Returns:
            Maximum drawdown as negative value
        """
        cumulative = (1 + returns).cumprod()
        running_max = cumulative.expanding().max()
        drawdown = (cumulative - running_max) / running_max
        return drawdown.min()
    
    def calculate_sharpe_ratio(self, returns: pd.Series, risk_free_rate: float = 0.02) -> float:
        """
        Calculate Sharpe Ratio.
        
        Args:
            returns: Series of returns
            risk_free_rate: Annual risk-free rate
            
        Returns:
            Sharpe Ratio
        """
        excess_returns = returns - risk_free_rate / PERIODS_PER_YEAR  # annual -> per-period
        return excess_returns.mean() / excess_returns.std() * np.sqrt(PERIODS_PER_YEAR)
    
    def check_position_limits(self, positions: dict, capital: float, max_allocation: float = 0.2) -> bool:
        """
        Check if positions exceed maximum allocation limits.
        
        Args:
            positions: Dictionary of positions
            capital: Total capital
            max_allocation: Maximum allocation per position
            
        Returns:
            True if within limits, False otherwise
        """
        max_value = capital * max_allocation
        for position_value in positions.values():
            if abs(position_value) > max_value:
                return False
        return True
    
    def calculate_portfolio_volatility(self, returns: pd.Series) -> float:
        """
        Calculate portfolio volatility.

        Args:
            returns: Series of portfolio returns

        Returns:
            Annualized volatility
        """
        return returns.std() * np.sqrt(PERIODS_PER_YEAR)

    def correlation_matrix(self, returns_df: pd.DataFrame) -> pd.DataFrame:
        """Pairwise Pearson correlation matrix for a multi-column returns DataFrame."""
        return returns_df.corr()

    def diversification_ratio(
        self,
        weights: np.ndarray,
        cov_matrix: np.ndarray,
    ) -> float:
        """Weighted-average individual vol divided by portfolio vol.

        A ratio of 1.0 means no diversification benefit; higher is better.
        Returns 1.0 when portfolio volatility is zero (degenerate case).
        """
        vols = np.sqrt(np.diag(cov_matrix))
        weighted_avg_vol = float(np.dot(weights, vols))
        portfolio_var = float(weights @ cov_matrix @ weights)
        portfolio_vol = float(np.sqrt(max(portfolio_var, 0.0)))
        if portfolio_vol == 0.0:
            return 1.0
        return weighted_avg_vol / portfolio_vol

    def concentration_check(
        self,
        weights: np.ndarray,
        max_single_weight: float = 0.4,
    ) -> bool:
        """Return True if no single weight exceeds the concentration limit."""
        return bool(np.max(np.abs(weights)) <= max_single_weight)


# ---------------------------------------------------------------------------
# Account rules + PropFirmRiskGuard (live-side, mirrors backtesting/_update_propfirm)
# ---------------------------------------------------------------------------

from dataclasses import dataclass


@dataclass
class AccountRules:
    account_size: float
    daily_loss_limit_pct: float
    drawdown_limit_pct: float
    max_leverage: float

    @classmethod
    def from_settings_yaml(cls, section: dict):
        """Construct from a `strategy` or `account` config dict.

        The config dict is expected to contain the keys shown in the dataclass
        (names may be percent-floats for limits e.g. 0.03). Caller must validate.
        """
        return cls(
            account_size=float(section["account_size"]),
            daily_loss_limit_pct=float(section["daily_loss_limit_pct"]),
            drawdown_limit_pct=float(section["drawdown_limit_pct"]),
            max_leverage=float(section["max_leverage"]),
        )


class PropFirmRiskGuard:
    """Live account guard implementing halt + dynamic max-notional scaling.

    Mirrors the halt logic in `backtesting/engine._update_propfirm` but kept
    dependency-free so it can be used by the dashboard and live helpers.

    Scaling rule for `.max_notional()`:
      - Let `daily_limit` = account_size * daily_loss_limit_pct
      - Let `drawdown_floor` = account_size * drawdown_limit_pct
      - Compute dollars-to-daily = (equity - daily_start_equity) negative means loss
      - When account has lost >= 50% of the distance to either floor, scale
        max_notional linearly down to 0 at the floor. In formula form:

        ratio = min( (dist_to_floor - 0.5 * dist_to_floor) / (0.5 * dist_to_floor), 1.0 )
        max_notional = account_size * max_leverage * ratio

    The above is implemented clearly below (no magic constants embedded).
    """

    def __init__(self, account_rules: AccountRules, equity: float, daily_start_equity: float, peak_equity: float):
        self.rules = account_rules
        self.current_equity = float(equity)
        self.daily_start_equity = float(daily_start_equity)
        self.peak_equity = float(peak_equity)

    def check(self, current_equity: float) -> dict:
        """Return halt status and distances in dollars.

        Returns: { halted: bool, reason: str|None, distance_to_daily_limit: float,
                   distance_to_drawdown_floor: float, current_drawdown_pct: float }
        """
        acct = self.rules
        daily_limit = acct.account_size * acct.daily_loss_limit_pct
        drawdown_floor_amount = acct.account_size * acct.drawdown_limit_pct
        drawdown_floor_equity = acct.account_size * (1.0 - acct.drawdown_limit_pct)

        daily_pnl = current_equity - self.daily_start_equity
        distance_to_daily_limit = daily_limit + min(0.0, daily_pnl) * -1.0  # dollars until hit (positive if not hit)

        # Mirror backtesting.engine._update_propfirm exactly: the halt uses a fixed
        # floor at initial_capital * (1 - drawdown_limit_pct), not a trailing peak-equity drawdown.
        drawdown = max(0.0, self.peak_equity - current_equity)
        distance_to_drawdown_floor = current_equity - drawdown_floor_equity

        current_drawdown_pct = (drawdown / acct.account_size) * 100.0 if acct.account_size > 0 else 0.0

        halted = False
        reason = None
        if distance_to_daily_limit <= 0:
            halted = True
            reason = "daily_loss_limit"
        elif distance_to_drawdown_floor <= 0:
            halted = True
            reason = "drawdown_floor"

        return {
            "halted": halted,
            "reason": reason,
            "distance_to_daily_limit": round(distance_to_daily_limit, 2),
            "distance_to_drawdown_floor": round(distance_to_drawdown_floor, 2),
            "current_drawdown_pct": round(current_drawdown_pct, 3),
        }

    def max_notional(self, current_equity: float) -> float:
        """Return a reduced max notional as the account nears its floors.

        Linear scaling behaviour (per-floor) implemented as:
          - If distance_to_floor >= half_distance: full notional allowed.
          - If 0 < distance_to_floor < half_distance: scale linearly from 0 -> 1.
          - If distance_to_floor <= 0: zero allowed.

        We compute the per-floor scale and take the minimum (most conservative).
        """
        acct = self.rules
        base_max = acct.account_size * acct.max_leverage

        # compute distances
        daily_limit = acct.account_size * acct.daily_loss_limit_pct
        drawdown_floor_amount = acct.account_size * acct.drawdown_limit_pct
        drawdown_floor_equity = acct.account_size * (1.0 - acct.drawdown_limit_pct)

        daily_pnl = current_equity - self.daily_start_equity
        dist_daily = daily_limit + min(0.0, daily_pnl) * -1.0
        dist_drawdown = max(0.0, current_equity - drawdown_floor_equity)

        def scale_from_distance(dist, floor_value):
            # floor_value is the absolute dollar distance from zero at which we
            # consider the "floor". If floor_value==0 (degenerate), return 0.
            if floor_value <= 0:
                return 0.0
            half = 0.5 * floor_value
            if dist <= 0:
                return 0.0
            if dist >= half:
                return 1.0
            # 0 < dist < half -> linearly scale from 0 to 1
            return dist / half

        # note: half-distance to daily_limit = 0.5 * daily_limit
        scale_daily = scale_from_distance(dist_daily, daily_limit)
        scale_draw = scale_from_distance(dist_drawdown, drawdown_floor_amount)

        scale = min(scale_daily, scale_draw)
        return round(base_max * scale, 2)
