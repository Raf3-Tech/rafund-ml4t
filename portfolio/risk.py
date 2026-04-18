"""
Risk management.

This module handles risk calculations, monitoring, and constraints.
"""

import pandas as pd
import numpy as np


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
        excess_returns = returns - risk_free_rate / 252  # Convert annual to daily
        return excess_returns.mean() / excess_returns.std() * np.sqrt(252)
    
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
        return returns.std() * np.sqrt(252)
