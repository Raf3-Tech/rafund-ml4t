"""
Model training pipeline.

This module handles training of ML models for trading predictions.
"""

import pandas as pd
import numpy as np
from typing import Dict, Tuple


def prepare_training_data(features: pd.DataFrame, target: pd.Series, test_size: float = 0.2) -> Tuple[pd.DataFrame, pd.DataFrame, pd.Series, pd.Series]:
    """
    Prepare data for model training with train/test split.
    
    Args:
        features: Feature matrix
        target: Target variable
        test_size: Proportion of data for testing
        
    Returns:
        Tuple of (X_train, X_test, y_train, y_test)
    """
    n = len(features)
    split_idx = int(n * (1 - test_size))
    
    X_train = features.iloc[:split_idx]
    X_test = features.iloc[split_idx:]
    y_train = target.iloc[:split_idx]
    y_test = target.iloc[split_idx:]
    
    return X_train, X_test, y_train, y_test


def cross_validate(model, X: pd.DataFrame, y: pd.Series, n_splits: int = 5) -> Dict[str, float]:
    """
    Perform k-fold cross-validation.
    
    Args:
        model: ML model with fit and score methods
        X: Feature matrix
        y: Target variable
        n_splits: Number of folds
        
    Returns:
        Dictionary with cross-validation scores
    """
    scores = []
    n = len(X)
    fold_size = n // n_splits
    
    for i in range(n_splits):
        start_idx = i * fold_size
        end_idx = (i + 1) * fold_size if i < n_splits - 1 else n
        
        X_val = X.iloc[start_idx:end_idx]
        y_val = y.iloc[start_idx:end_idx]
        X_train = pd.concat([X.iloc[:start_idx], X.iloc[end_idx:]])
        y_train = pd.concat([y.iloc[:start_idx], y.iloc[end_idx:]])
        
        model.fit(X_train, y_train)
        score = model.score(X_val, y_val)
        scores.append(score)
    
    return {
        'mean_score': np.mean(scores),
        'std_score': np.std(scores),
        'fold_scores': scores
    }
