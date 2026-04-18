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
    
    def __init__(self, entry_threshold: float = 2.0, exit_threshold: float = 0.5, 
                 use_fixed_window: bool = True, fixed_window_size: int = 60):
        """
        Initialize statistical arbitrage strategy.
        
        Args:
            entry_threshold: Z-score threshold for entry (default: 2.0 std deviations)
            exit_threshold: Z-score threshold for exit (default: 0.5 std deviations)
            use_fixed_window: If True, use fixed training window; if False, use rolling (deprecated)
            fixed_window_size: Size of fixed training window (default: 60 days)
        """
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self.use_fixed_window = use_fixed_window
        self.fixed_window_size = fixed_window_size
        self.signals = None
        self.fixed_mean = None  # Will store fixed mean from training period
        self.fixed_std = None   # Will store fixed std from training period
        
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
    
    def calculate_z_score(self, spread: pd.Series, window: int = 20, training_end_idx: int = None) -> pd.Series:
        """
        Calculate Z-score for the spread.
        
        Supports two modes:
        1. Fixed window (RECOMMENDED): Uses statistics from training period only
           - Eliminates window drift
           - More stable baseline
           
        2. Rolling window (DEPRECATED): Updates baseline daily
           - Can cause false signals (window chasing)
           - Use only for comparison
        
        Formula: z = (spread - mean) / std
        
        Args:
            spread: Series of spread values
            window: Window size (used only if use_fixed_window=False)
            training_end_idx: End index of training period (for fixed window mode)
            
        Returns:
            Series of Z-score values
        """
        if self.use_fixed_window:
            # FIXED WINDOW MODE (recommended)
            # Use only the training period to calculate mean and std
            if training_end_idx is None:
                training_end_idx = min(self.fixed_window_size, len(spread))
            
            training_spread = spread.iloc[:training_end_idx]
            mean = training_spread.mean()
            std = training_spread.std()
            
            # Store for later reference
            self.fixed_mean = mean
            self.fixed_std = std
            
            # Calculate z-score using FIXED stats (no rolling)
            z_score = (spread - mean) / std
            
        else:
            # ROLLING WINDOW MODE (deprecated, problematic)
            # This is the old approach - kept for backwards compatibility
            mean = spread.rolling(window).mean()
            std = spread.rolling(window).std()
            z_score = (spread - mean) / std
        
        return z_score
    
    def generate_signals(self, z_score: pd.Series, spread: pd.Series = None) -> pd.DataFrame:
        """
        Generate trading signals based on Z-score.
        
        Signal Rules:
            - 1: Long spread (buy A, short B) when z < -entry_threshold
            - -1: Short spread (short A, buy B) when z > entry_threshold
            - 0: Close when |z| < exit_threshold AND spread has reverted
            
        Args:
            z_score: Series of Z-score values
            spread: Series of spread values (required for reversion validation)
            
        Returns:
            DataFrame with signals and positions
        """
        signals = pd.DataFrame(index=z_score.index)
        signals['z_score'] = z_score
        if spread is not None:
            signals['spread'] = spread
        
        # Initialize signal column
        signals['signal'] = 0
        signals['position_a'] = 0
        signals['position_b'] = 0
        signals['entry_spread'] = np.nan  # Track entry spread for reversion validation
        
        # Entry conditions
        signals.loc[z_score < -self.entry_threshold, 'signal'] = 1    # Long A, Short B
        signals.loc[z_score > self.entry_threshold, 'signal'] = -1    # Short A, Long B
        
        # Track entry spread when position opens
        in_position = False
        position_type = 0
        entry_spread = None
        
        for idx in signals.index:
            current_signal = signals.loc[idx, 'signal']
            
            # Entry: position changed from 0 to non-zero
            if not in_position and current_signal != 0:
                entry_spread = signals.loc[idx, 'spread'] if spread is not None else np.nan
                signals.loc[idx, 'entry_spread'] = entry_spread
                in_position = True
                position_type = current_signal
            
            # Maintain position until exit
            elif in_position and current_signal == 0:
                signals.loc[idx, 'entry_spread'] = entry_spread
                in_position = False
            elif in_position:
                signals.loc[idx, 'entry_spread'] = entry_spread
        
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
        
        For each trade, validates whether actual spread reversion occurred
        (not just z-score threshold crossing).
        
        Args:
            signals: DataFrame with signals
            
        Returns:
            DataFrame with trade information including reversion validation
        """
        trades = []
        prev_signal = 0
        entry_date = None
        entry_z = None
        entry_spread = None
        position_type = None
        
        for date, row in signals.iterrows():
            current_signal = row['signal']
            current_z = row['z_score']
            current_spread = row.get('spread', np.nan)
            
            # Entry
            if prev_signal == 0 and current_signal != 0:
                entry_date = date
                entry_z = current_z
                entry_spread = current_spread
                position_type = 'Long' if current_signal == 1 else 'Short'
            
            # Exit
            elif prev_signal != 0 and current_signal == 0:
                # Validate whether spread actually reverted
                spread_reverted = False
                reversion_distance = np.nan
                
                if not np.isnan(entry_spread) and not np.isnan(current_spread):
                    # For long positions: spread should decrease (improve)
                    if position_type == 'Long':
                        spread_reverted = (current_spread < entry_spread)
                        reversion_distance = entry_spread - current_spread
                    # For short positions: spread should increase
                    elif position_type == 'Short':
                        spread_reverted = (current_spread > entry_spread)
                        reversion_distance = current_spread - entry_spread
                
                trades.append({
                    'entry_date': entry_date,
                    'exit_date': date,
                    'entry_z': entry_z,
                    'exit_z': current_z,
                    'entry_spread': entry_spread,
                    'exit_spread': current_spread,
                    'position_type': position_type,
                    'duration_days': (date - entry_date).days,
                    'spread_reverted': spread_reverted,
                    'reversion_distance': reversion_distance,
                    'signal_validity': 'VALID' if spread_reverted else 'FALSE_SIGNAL (window drift)'
                })
                entry_date = None
                entry_z = None
                entry_spread = None
                position_type = None
            
            prev_signal = current_signal
        
        return pd.DataFrame(trades)
