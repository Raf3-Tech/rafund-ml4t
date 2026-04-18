"""
Model inference and prediction.

This module handles making predictions with trained models.
"""

import pandas as pd
import numpy as np


class Predictor:
    """Wrapper for making predictions with trained models."""
    
    def __init__(self, model):
        """
        Initialize predictor.
        
        Args:
            model: Trained model object
        """
        self.model = model
        
    def predict(self, X: pd.DataFrame) -> np.ndarray:
        """
        Generate predictions.
        
        Args:
            X: Feature matrix
            
        Returns:
            Array of predictions
        """
        return self.model.predict(X)
    
    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        """
        Generate probability predictions (if applicable).
        
        Args:
            X: Feature matrix
            
        Returns:
            Array of probability predictions
        """
        if hasattr(self.model, 'predict_proba'):
            return self.model.predict_proba(X)
        else:
            raise ValueError("Model does not support probability predictions")
    
    def get_feature_importance(self) -> dict:
        """
        Get feature importance if available.
        
        Returns:
            Dictionary of feature importances
        """
        if hasattr(self.model, 'feature_importances_'):
            return {'importances': self.model.feature_importances_}
        elif hasattr(self.model, 'coef_'):
            return {'coefficients': self.model.coef_}
        else:
            return {}
