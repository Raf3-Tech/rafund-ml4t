"""
Factor model construction and management.

This module handles creation and maintenance of factor models
used in quantitative trading strategies.
"""

import pandas as pd
import numpy as np
from sklearn.linear_model import LinearRegression


class FactorModel:
    """Base class for factor models."""
    
    def __init__(self, name: str):
        """
        Initialize factor model.
        
        Args:
            name: Name of the factor model
        """
        self.name = name
        self.model = None
        self.coefficients = None
        
    def fit(self, X: pd.DataFrame, y: pd.Series):
        """
        Fit the factor model.
        
        Args:
            X: Feature matrix
            y: Target variable
        """
        self.model = LinearRegression()
        self.model.fit(X, y)
        self.coefficients = self.model.coef_
        
    def predict(self, X: pd.DataFrame) -> np.ndarray:
        """
        Generate predictions.
        
        Args:
            X: Feature matrix
            
        Returns:
            Predicted values
        """
        if self.model is None:
            raise ValueError("Model not fitted. Call fit() first.")
        return self.model.predict(X)
    
    def get_residuals(self, X: pd.DataFrame, y: pd.Series) -> pd.Series:
        """
        Calculate residuals.
        
        Args:
            X: Feature matrix
            y: Actual values
            
        Returns:
            Series of residuals
        """
        predictions = self.predict(X)
        return y - predictions


def cointegration_regression(asset_a: pd.Series, asset_b: pd.Series) -> dict:
    """
    Perform cointegration regression between two assets.
    
    Args:
        asset_a: Price series for asset A
        asset_b: Price series for asset B
        
    Returns:
        Dictionary with regression results including hedge ratio
    """
    log_a = np.log(asset_a)
    log_b = np.log(asset_b)
    
    X = log_b.values.reshape(-1, 1)
    y = log_a.values
    
    model = LinearRegression()
    model.fit(X, y)
    
    return {
        'hedge_ratio': model.coef_[0],
        'intercept': model.intercept_,
        'r_squared': model.score(X, y),
        'model': model
    }
