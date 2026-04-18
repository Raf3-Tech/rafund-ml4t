"""
Statistical Arbitrage Strategy.

This module implements the core statistical arbitrage strategy
based on mean-reverting spreads between correlated assets.

Mathematical Framework:
    Spread = log(P_A) - β * log(P_B)
    Z-score = (Spread - μ) / σ
    
Trading Rules:
    - Long spread (buy A, sell B) when z-score < -2
    - Short spread (sell A, buy B) when z-score > 2
    - Exit when z-score reverts to 0
"""

import pandas as pd
import numpy as np
from typing import Dict, Tuple


class StatArbStrategy:
    """Statistical Arbitrage Strategy Implementation."""
    
    def __init__(self, entry_threshold: float = 2.0, exit_threshold: float = 0.5):
        """
        Initialize statistical arbitrage strategy.
        
        Args:
            entry_threshold: Z-score threshold for entry (default: 2.0 std deviations)
            exit_threshold: Z-score threshold for exit (default: 0.5 std deviations)
        """
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self.signals = None
        
    def calculate_spread(self, price_a: pd.Series, price_b: pd.Series, hedge_ratio: float) -> pd.Series:
        """
        Calculate the spread between two assets.
        
        Formula: spread = log(P_A) - β * log(P_B)
        
        Args:
            price_a: Price series for asset A
            price_b: Price series for asset B
            hedge_ratio: Hedge ratio (β) from regression
            
        Returns:
            Series of spread values
        """
        log_a = np.log(price_a)
        log_b = np.log(price_b)
        return log_a - hedge_ratio * log_b
    
    def calculate_z_score(self, spread: pd.Series, window: int = 20) -> pd.Series:
        """
        Calculate Z-score for the spread.
        
        Formula: z = (spread - mean) / std
        
        Args:
            spread: Series of spread values
            window: Rolling window for mean and std calculation
            
        Returns:
            Series of Z-score values
        """
        mean = spread.rolling(window).mean()
        std = spread.rolling(window).std()
        z_score = (spread - mean) / std
        return z_score
    
    def generate_signals(self, z_score: pd.Series) -> pd.DataFrame:
        """
        Generate trading signals based on Z-score.
        
        Signal Rules:
            - 1: Long spread (buy A, short B) when z < -entry_threshold
            - -1: Short spread (short A, buy B) when z > entry_threshold
            - 0: Close when |z| < exit_threshold
            
        Args:
            z_score: Series of Z-score values
            
        Returns:
            DataFrame with signals and positions
        """
        signals = pd.DataFrame(index=z_score.index)
        signals['z_score'] = z_score
        
        # Initialize signal column
        signals['signal'] = 0
        signals['position_a'] = 0
        signals['position_b'] = 0
        
        # Entry conditions
        signals.loc[z_score < -self.entry_threshold, 'signal'] = 1    # Long A, Short B
        signals.loc[z_score > self.entry_threshold, 'signal'] = -1    # Short A, Long B
        
        # Exit conditions (simple: when |z| < exit_threshold)
        signals.loc[np.abs(z_score) < self.exit_threshold, 'signal'] = 0
        
        # Forward fill signals to maintain position
        signals['signal'] = signals['signal'].ffill().fillna(0)
        
        # Position sizes (normalized)
        signals['position_a'] = signals['signal']
        signals['position_b'] = -signals['signal']
        
        self.signals = signals
        return signals
    
    def get_trades(self, signals: pd.DataFrame) -> pd.DataFrame:
        """
        Extract trade entries and exits from signals.
        
        Args:
            signals: DataFrame with signals
            
        Returns:
            DataFrame with trade information
        """
        trades = []
        prev_signal = 0
        entry_date = None
        entry_z = None
        
        for date, row in signals.iterrows():
            current_signal = row['signal']
            current_z = row['z_score']
            
            # Entry
            if prev_signal == 0 and current_signal != 0:
                entry_date = date
                entry_z = current_z
            
            # Exit
            elif prev_signal != 0 and current_signal == 0:
                trades.append({
                    'entry_date': entry_date,
                    'exit_date': date,
                    'entry_z': entry_z,
                    'exit_z': current_z,
                    'position_type': 'Long' if prev_signal == 1 else 'Short',
                    'duration_days': (date - entry_date).days
                })
                entry_date = None
                entry_z = None
            
            prev_signal = current_signal
        
        return pd.DataFrame(trades)
