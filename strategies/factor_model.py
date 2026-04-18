"""
Factor-based trading strategy.

This module implements trading strategies based on factor models
and multi-factor frameworks.
"""

import pandas as pd
import numpy as np


class FactorStrategy:
    """Factor-based trading strategy."""
    
    def __init__(self):
        """Initialize factor strategy."""
        self.factors = {}
        self.factor_weights = {}
        
    def add_factor(self, name: str, values: pd.Series, weight: float = 1.0):
        """
        Add a factor to the strategy.
        
        Args:
            name: Factor name
            values: Factor values as a time series
            weight: Factor weight in composite score
        """
        self.factors[name] = values
        self.factor_weights[name] = weight
    
    def compute_composite_score(self) -> pd.Series:
        """
        Compute composite score from all factors.
        
        Returns:
            Series of composite scores
        """
        if not self.factors:
            raise ValueError("No factors added. Use add_factor() first.")
        
        scores = None
        total_weight = sum(self.factor_weights.values())
        
        for factor_name, factor_values in self.factors.items():
            weight = self.factor_weights[factor_name]
            normalized = factor_values / total_weight
            
            if scores is None:
                scores = normalized * weight
            else:
                scores = scores.add(normalized * weight, fill_value=0)
        
        return scores
    
    def generate_signals(self, scores: pd.Series, upper_threshold: float = 0.7, lower_threshold: float = 0.3) -> pd.Series:
        """
        Generate trading signals from composite scores.
        
        Args:
            scores: Series of composite scores
            upper_threshold: Upper threshold for long signal
            lower_threshold: Lower threshold for short signal
            
        Returns:
            Series of signals (1: long, -1: short, 0: neutral)
        """
        signals = pd.Series(0, index=scores.index)
        signals[scores > upper_threshold] = 1    # Long
        signals[scores < lower_threshold] = -1   # Short
        
        return signals
