"""
Portfolio optimization.

This module handles portfolio construction, asset allocation,
and risk-return optimization.
"""

import pandas as pd
import numpy as np


class PortfolioOptimizer:
    """Portfolio optimization and allocation."""
    
    def __init__(self, initial_capital: float = 100000, max_position_size: float = 0.2):
        """
        Initialize portfolio optimizer.
        
        Args:
            initial_capital: Initial capital in base currency
            max_position_size: Maximum position size as fraction of capital
        """
        self.initial_capital = initial_capital
        self.max_position_size = max_position_size
        self.positions = {}
        
    def calculate_position_size(self, signal: int, price: float, capital: float) -> int:
        """
        Calculate position size based on signal and capital.
        
        Args:
            signal: Trading signal (1 for long, -1 for short)
            price: Asset price
            capital: Available capital
            
        Returns:
            Position size (number of units)
        """
        max_allocation = capital * self.max_position_size
        position_value = max_allocation
        position_size = int(position_value / price)
        return signal * position_size
    
    def allocate_capital(self, prices: pd.Series, signals: pd.Series, capital: float) -> dict:
        """
        Allocate capital across assets based on signals.
        
        Args:
            prices: Current prices by asset
            signals: Trading signals by asset
            capital: Available capital
            
        Returns:
            Dictionary of positions by asset
        """
        positions = {}
        allocated = 0
        
        for asset, signal in signals.items():
            if asset in prices.index:
                price = prices[asset]
                position_size = self.calculate_position_size(signal, price, capital)
                positions[asset] = position_size
                allocated += abs(position_size * price)
        
        return positions
    
    def calculate_portfolio_value(self, positions: dict, prices: pd.Series) -> float:
        """
        Calculate current portfolio value.
        
        Args:
            positions: Dictionary of positions
            prices: Current prices
            
        Returns:
            Total portfolio value
        """
        value = 0
        for asset, quantity in positions.items():
            if asset in prices.index:
                value += quantity * prices[asset]
        return value
    
    def rebalance(self, current_positions: dict, target_signals: pd.Series, prices: pd.Series, capital: float) -> dict:
        """
        Rebalance portfolio to match target signals.
        
        Args:
            current_positions: Current positions
            target_signals: Target signals
            prices: Current prices
            capital: Available capital
            
        Returns:
            Dictionary of required trades
        """
        target_positions = self.allocate_capital(prices, target_signals, capital)
        trades = {}
        
        for asset in set(list(current_positions.keys()) + list(target_positions.keys())):
            current = current_positions.get(asset, 0)
            target = target_positions.get(asset, 0)
            trade_size = target - current
            
            if trade_size != 0:
                trades[asset] = trade_size
        
        return trades
