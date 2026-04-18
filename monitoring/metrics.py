"""
Performance metrics and monitoring.

This module calculates and tracks key performance metrics
for strategy evaluation and risk monitoring.
"""

import pandas as pd
import numpy as np


class MetricsCalculator:
    """Calculate and track performance metrics."""
    
    @staticmethod
    def calculate_returns(prices: pd.Series) -> pd.Series:
        """
        Calculate simple returns.
        
        Args:
            prices: Series of prices
            
        Returns:
            Series of returns
        """
        return prices.pct_change()
    
    @staticmethod
    def calculate_log_returns(prices: pd.Series) -> pd.Series:
        """
        Calculate log returns.
        
        Args:
            prices: Series of prices
            
        Returns:
            Series of log returns
        """
        return np.log(prices / prices.shift(1))
    
    @staticmethod
    def calculate_cumulative_returns(returns: pd.Series) -> pd.Series:
        """
        Calculate cumulative returns.
        
        Args:
            returns: Series of returns
            
        Returns:
            Series of cumulative returns
        """
        return (1 + returns).cumprod() - 1
    
    @staticmethod
    def calculate_rolling_volatility(returns: pd.Series, window: int = 20) -> pd.Series:
        """
        Calculate rolling volatility.
        
        Args:
            returns: Series of returns
            window: Rolling window
            
        Returns:
            Series of volatility values
        """
        return returns.rolling(window).std()
    
    @staticmethod
    def calculate_sortino_ratio(returns: pd.Series, risk_free_rate: float = 0.02) -> float:
        """
        Calculate Sortino Ratio (downside risk focus).
        
        Args:
            returns: Series of returns
            risk_free_rate: Annual risk-free rate
            
        Returns:
            Sortino Ratio
        """
        excess_returns = returns - risk_free_rate / 252
        downside = returns[returns < 0]
        downside_std = downside.std()
        
        if downside_std == 0:
            return 0
        
        return excess_returns.mean() / downside_std * np.sqrt(252)
    
    @staticmethod
    def calculate_calmar_ratio(returns: pd.Series) -> float:
        """
        Calculate Calmar Ratio (return/max drawdown).
        
        Args:
            returns: Series of returns
            
        Returns:
            Calmar Ratio
        """
        annual_return = returns.mean() * 252
        
        cumulative = (1 + returns).cumprod()
        running_max = cumulative.expanding().max()
        drawdown = (cumulative - running_max) / running_max
        max_drawdown = abs(drawdown.min())
        
        if max_drawdown == 0:
            return 0
        
        return annual_return / max_drawdown
    
    @staticmethod
    def calculate_sharpe_ratio(returns: pd.Series, risk_free_rate: float = 0.02) -> float:
        """
        Calculate Sharpe Ratio (risk-adjusted return).
        
        For daily returns: annualization factor = sqrt(252)
        For monthly returns: annualization factor = sqrt(12)
        
        Args:
            returns: Series of returns (daily or other frequency)
            risk_free_rate: Annual risk-free rate (default: 2%)
            
        Returns:
            Annualized Sharpe Ratio
        """
        if len(returns) < 2 or returns.std() == 0:
            return 0
        
        # Assume daily returns, use 252 trading days per year
        mean_daily_return = returns.mean()
        std_daily_return = returns.std()
        
        sharpe_ratio = (mean_daily_return / std_daily_return) * np.sqrt(252)
        
        # Optional: subtract risk-free rate component
        # This is a simplified version; strict formula would be:
        # sharpe = (mean_annual_return - annual_rf_rate) / annual_volatility
        
        return sharpe_ratio
    
    @staticmethod
    def calculate_win_rate(trades: list) -> float:
        """
        Calculate win rate from trades.
        
        Args:
            trades: List of trade records with 'pnl' field
            
        Returns:
            Win rate as percentage
        """
        if not trades:
            return 0
        
        winning = len([t for t in trades if t.get('pnl', 0) > 0])
        return winning / len(trades)
