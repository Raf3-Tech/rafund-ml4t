"""
Price-based feature engineering.

This module handles creation of features based on price data,
including technical indicators and price-derived metrics.
"""

import pandas as pd
import numpy as np
import logging
from typing import Tuple

logger = logging.getLogger(__name__)

# Try to import statsmodels for ADF test
try:
    from statsmodels.tsa.stattools import adfuller
    HAS_STATSMODELS = True
except ImportError:
    HAS_STATSMODELS = False
    logger.warning("statsmodels not available - stationarity tests disabled")


def calculate_returns(prices: pd.DataFrame) -> pd.Series:
    """
    Calculate log returns from price data.
    
    Args:
        prices: Series or DataFrame of prices
        
    Returns:
        Series of log returns
    """
    return np.log(prices / prices.shift(1))


def calculate_volatility(returns: pd.Series, window: int = 20) -> pd.Series:
    """
    Calculate rolling volatility.
    
    Args:
        returns: Series of returns
        window: Rolling window size
        
    Returns:
        Series of volatility values
    """
    return returns.rolling(window).std()


def calculate_moving_average(prices: pd.Series, window: int = 20) -> pd.Series:
    """
    Calculate simple moving average.
    
    Args:
        prices: Series of prices
        window: Moving average window size
        
    Returns:
        Series of moving averages
    """
    return prices.rolling(window).mean()


def calculate_rsq(y: pd.Series, y_pred: pd.Series) -> float:
    """
    Calculate R-squared between actual and predicted values.
    
    Args:
        y: Actual values
        y_pred: Predicted values
        
    Returns:
        R-squared value
    """
    ss_res = ((y - y_pred) ** 2).sum()
    ss_tot = ((y - y.mean()) ** 2).sum()
    return 1 - (ss_res / ss_tot)


def calculate_spread_features(
    prices_a: pd.Series,
    prices_b: pd.Series,
    window: int = 20,
    include_entropy: bool = True,
    entropy_window: int = 30,
    entropy_embedding: int = 3,
    entropy_threshold: float = 0.95,
) -> pd.DataFrame:
    """
    Calculate spread-based features for pairs trading.

    Args:
        prices_a: Price series for asset A
        prices_b: Price series for asset B
        window: Rolling window for mean/std calculations
        include_entropy: Append causal permutation-entropy features (Phase 1)
        entropy_window: Rolling window for permutation entropy
        entropy_embedding: Ordinal-pattern embedding dimension
        entropy_threshold: PE level above which a regime is "high" / untradeable

    Returns:
        DataFrame with spread, mean, std, z-score, hedge_ratio and (optionally)
        the permutation-entropy columns perm_entropy/entropy_rank/entropy_regime.
    """
    # Align series
    df = pd.DataFrame({
        'price_a': prices_a,
        'price_b': prices_b
    }).dropna()
    
    if df.empty:
        return pd.DataFrame()
    
    # Normalize prices to start at 1 for comparison
    df['norm_a'] = df['price_a'] / df['price_a'].iloc[0]
    df['norm_b'] = df['price_b'] / df['price_b'].iloc[0]
    
    # Calculate spread
    df['spread'] = df['norm_a'] - df['norm_b']
    
    # Rolling statistics
    df['spread_mean'] = df['spread'].rolling(window).mean()
    df['spread_std'] = df['spread'].rolling(window).std()
    
    # Z-score
    # Guard against divide-by-zero: when the rolling spread_std is 0 (a flat /
    # constant spread over the window) the raw division produces inf/-inf/NaN
    # which would propagate into signals and any model trained on these
    # features. A zero std means no measurable deviation, so the z-score is 0.0.
    # Warm-up rows (where spread_mean/spread_std are NaN because the rolling
    # window isn't full yet) keep their NaN, matching the existing convention.
    df['z_score'] = (df['spread'] - df['spread_mean']) / df['spread_std']
    df['z_score'] = df['z_score'].mask(df['spread_std'] == 0, 0.0)
    
    # Hedge ratio (β) via an EXPANDING, strictly causal estimate.
    #
    # LEAKAGE FIX: the previous implementation computed a single cov/var over
    # the ENTIRE series and broadcast it to every row. That injects future
    # information (look-ahead) into a feature that is used both for model
    # training and for position sizing, and it produces a zero-variance column
    # whose feature importance is meaningless. Here β at row t is estimated
    # from only the returns observed up to and including t (expanding window),
    # so no row ever sees the future. Warm-up rows (insufficient history) and
    # degenerate (zero-variance B) windows fall back to a unit hedge ratio.
    log_returns_a = np.log(df['price_a'] / df['price_a'].shift(1))
    log_returns_b = np.log(df['price_b'] / df['price_b'].shift(1))
    min_obs = max(2, window)
    cov_ab = log_returns_a.expanding(min_periods=min_obs).cov(log_returns_b)
    var_b = log_returns_b.expanding(min_periods=min_obs).var()
    hedge_ratio = (cov_ab / var_b).where(var_b > 0).fillna(1.0)
    df['hedge_ratio'] = hedge_ratio

    out_cols = ['spread', 'spread_mean', 'spread_std', 'z_score', 'hedge_ratio']

    if include_entropy:
        # Causal permutation-entropy features on the spread (Phase 1, alpha
        # roadmap). Local import keeps the dependency lazy and avoids any
        # import-order coupling within the features package.
        from features.permutation_entropy import entropy_features

        ent = entropy_features(
            df['spread'],
            window=entropy_window,
            embedding_dimension=entropy_embedding,
            threshold=entropy_threshold,
        )
        df['perm_entropy'] = ent['perm_entropy']
        df['entropy_rank'] = ent['entropy_rank']
        df['entropy_regime'] = ent['entropy_regime']
        out_cols += ['perm_entropy', 'entropy_rank', 'entropy_regime']

    return df[out_cols]


def calculate_momentum_features(prices: pd.Series, window: int = 20) -> pd.DataFrame:
    """
    Calculate momentum-based features.
    
    Args:
        prices: Series of prices
        window: Momentum calculation window
        
    Returns:
        DataFrame with momentum features
    """
    df = pd.DataFrame({'close': prices})
    
    # Returns
    df['returns'] = df['close'].pct_change()
    df['log_returns'] = np.log(df['close'] / df['close'].shift(1))
    
    # Momentum
    df['momentum'] = df['close'] - df['close'].shift(window)
    df['momentum_pct'] = df['momentum'] / df['close'].shift(window)
    
    # Rate of change
    df['roc'] = ((df['close'] - df['close'].shift(window)) / df['close'].shift(window)) * 100
    
    # Moving averages
    df['sma_20'] = df['close'].rolling(20).mean()
    df['sma_50'] = df['close'].rolling(50).mean()
    
    # Relative Strength Index (RSI)
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))
    
    # Bollinger Bands
    df['bb_middle'] = df['close'].rolling(20).mean()
    df['bb_std'] = df['close'].rolling(20).std()
    df['bb_upper'] = df['bb_middle'] + (df['bb_std'] * 2)
    df['bb_lower'] = df['bb_middle'] - (df['bb_std'] * 2)
    
    # Volatility
    df['volatility'] = df['log_returns'].rolling(window).std()
    
    return df.drop('close', axis=1)


def calculate_single_asset_features(prices: pd.Series, symbol: str) -> pd.DataFrame:
    """
    Calculate all features for a single asset.
    
    Args:
        prices: Series of prices
        symbol: Symbol name
        
    Returns:
        DataFrame with all calculated features
    """
    features_df = calculate_momentum_features(prices)
    return features_df


def test_stationarity(series: pd.Series, pair_name: str = "") -> Tuple[bool, dict]:
    """
    Test if a series is stationary using ADF test.
    
    A stationary series is mean-reverting - critical for pairs trading.
    For stat arb to work, the spread MUST be stationary (p-value < 0.05).
    
    Args:
        series: Series to test for stationarity
        pair_name: Name for logging (e.g., "BTC/ETH")
        
    Returns:
        Tuple of (is_stationary: bool, test_results: dict)
    """
    if not HAS_STATSMODELS:
        logger.warning(f"Cannot test stationarity for {pair_name} - statsmodels not available")
        return True, {}  # Default to True if can't test
    
    if series.empty or len(series) < 20:
        logger.warning(f"Insufficient data for stationarity test on {pair_name}")
        return False, {'error': 'insufficient_data'}
    
    try:
        # Remove NaN values
        clean_series = series.dropna()
        
        if len(clean_series) < 20:
            return False, {'error': 'insufficient_data_after_cleaning'}
        
        # Run ADF test
        result = adfuller(clean_series, autolag='AIC')
        
        adf_stat = result[0]
        p_value = result[1]
        n_lags = result[2]
        n_obs = result[3]
        
        is_stationary = p_value < 0.05  # 95% confidence level
        
        test_results = {
            'adf_statistic': adf_stat,
            'p_value': p_value,
            'n_lags': n_lags,
            'n_obs': n_obs,
            'is_stationary': is_stationary
        }
        
        if is_stationary:
            logger.info(f"[VALID PAIR] {pair_name}: Spread is STATIONARY (p={p_value:.4f})")
        else:
            logger.warning(f"[INVALID PAIR] {pair_name}: Spread is NOT stationary (p={p_value:.4f}) - pairs trading won't work!")
        
        return is_stationary, test_results
        
    except Exception as e:
        logger.error(f"Error testing stationarity for {pair_name}: {str(e)}")
        return False, {'error': str(e)}
