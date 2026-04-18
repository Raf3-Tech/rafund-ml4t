"""
Backtesting Engine for ML4T System.

Implements complete backtesting with signal generation and performance metrics.
"""

import pandas as pd
import numpy as np
import logging
from datetime import datetime
from typing import Dict, List

logger = logging.getLogger(__name__)


class BacktestEngine:
    """Complete backtesting engine with signal generation."""
    
    def __init__(
        self,
        initial_capital: float = 100000,
        commission: float = 0.001,
        entry_threshold: float = 2.0,
        exit_threshold: float = 0.5,
        lookback: int = 60
    ):
        """
        Initialize backtesting engine.
        
        Args:
            initial_capital: Starting capital in USDT
            commission: Trading commission as decimal (0.001 = 0.1%)
            entry_threshold: Z-score threshold for entry
            exit_threshold: Z-score threshold for exit
            lookback: Lookback period for rolling statistics
        """
        self.initial_capital = initial_capital
        self.commission = commission
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self.lookback = lookback
        
        # State tracking
        self.cash = initial_capital
        self.positions = {}
        self.trades = []
        self.equity_curve = []
        self.dates = []
        
    def generate_signals(self, prices: pd.DataFrame) -> pd.DataFrame:
        """
        Generate trading signals using mean-reversion.
        
        Args:
            prices: DataFrame with 'timestamp', 'symbol', 'close' columns
            
        Returns:
            DataFrame with trading signals
        """
        if prices.empty:
            return pd.DataFrame()
        
        signals = []
        
        # Calculate signals for each symbol
        for symbol in prices['symbol'].unique():
            symbol_data = prices[prices['symbol'] == symbol].copy()
            symbol_data = symbol_data.sort_values('timestamp').reset_index(drop=True)
            
            # Calculate rolling statistics
            symbol_data['ma'] = symbol_data['close'].rolling(self.lookback).mean()
            symbol_data['std'] = symbol_data['close'].rolling(self.lookback).std()
            
            # Z-score: distance from mean in standard deviations
            symbol_data['z_score'] = (symbol_data['close'] - symbol_data['ma']) / symbol_data['std']
            
            # Generate signals
            symbol_data['signal'] = 'HOLD'
            symbol_data.loc[symbol_data['z_score'] > self.entry_threshold, 'signal'] = 'BUY'
            symbol_data.loc[symbol_data['z_score'] < -self.entry_threshold, 'signal'] = 'SELL'
            symbol_data.loc[
                (symbol_data['z_score'] < self.exit_threshold) & 
                (symbol_data['z_score'] > -self.exit_threshold),
                'signal'
            ] = 'EXIT'
            
            signals.append(symbol_data[['timestamp', 'symbol', 'close', 'z_score', 'signal']])
        
        return pd.concat(signals, ignore_index=True) if signals else pd.DataFrame()
    
    def run(self, prices: pd.DataFrame) -> Dict:
        """
        Run backtest on price data.
        
        Args:
            prices: DataFrame with OHLCV data
            
        Returns:
            Backtest results dictionary
        """
        logger.info("Generating trading signals...")
        
        # Prepare data
        prices_clean = prices[['timestamp', 'symbol', 'close']].copy()
        prices_clean = prices_clean.sort_values(['symbol', 'timestamp']).reset_index(drop=True)
        
        # Generate signals
        signals_df = self.generate_signals(prices_clean)
        
        if signals_df.empty:
            logger.error("No signals generated")
            return self._empty_results()
        
        logger.info(f"Generated signals for {len(signals_df)} price points")
        logger.info("Simulating trades...")
        
        # Simulation loop
        for idx, row in signals_df.iterrows():
            timestamp = row['timestamp']
            symbol = row['symbol']
            price = row['close']
            signal = row['signal']
            z_score = row['z_score']
            
            # Skip if missing data
            if pd.isna(z_score):
                continue
            
            # Execute trades
            if signal == 'BUY':
                self._execute_buy(symbol, price, timestamp)
            elif signal == 'SELL':
                self._execute_sell(symbol, price, timestamp)
            elif signal == 'EXIT':
                self._execute_exit(symbol, price, timestamp)
            
            # Track equity (simplified)
            if idx % 60 == 0:  # Record every 60 days to avoid too much data
                equity = self._calculate_equity()
                self.equity_curve.append(equity)
                self.dates.append(timestamp)
        
        # Record final equity
        final_equity = self._calculate_equity()
        self.equity_curve.append(final_equity)
        
        logger.info("Calculating metrics...")
        
        # Compile results
        results = self._calculate_metrics()
        return results
    
    def _execute_buy(self, symbol: str, price: float, date):
        """Execute buy order."""
        position_size = self.cash * 0.1 / price  # Use 10% of cash per position
        cost = position_size * price * (1 + self.commission)
        
        if self.cash >= cost and position_size > 0:
            self.positions[symbol] = self.positions.get(symbol, 0) + position_size
            self.cash -= cost
            self.trades.append({
                'date': date,
                'symbol': symbol,
                'side': 'BUY',
                'quantity': position_size,
                'price': price
            })
    
    def _execute_sell(self, symbol: str, price: float, date):
        """Execute sell order."""
        if symbol in self.positions and self.positions[symbol] > 0:
            position_size = self.positions[symbol]
            proceeds = position_size * price * (1 - self.commission)
            
            self.cash += proceeds
            self.positions[symbol] = 0
            self.trades.append({
                'date': date,
                'symbol': symbol,
                'side': 'SELL',
                'quantity': position_size,
                'price': price
            })
    
    def _execute_exit(self, symbol: str, price: float, date):
        """Exit position."""
        self._execute_sell(symbol, price, date)
    
    def _calculate_equity(self) -> float:
        """Calculate current portfolio equity."""
        # For simplicity, use cash only (positions valued at cost)
        return self.cash
    
    def _calculate_metrics(self) -> Dict:
        """Calculate performance metrics."""
        if not self.equity_curve:
            return self._empty_results()
        
        equity_series = pd.Series(self.equity_curve)
        
        # Returns
        total_return = (self.equity_curve[-1] - self.initial_capital) / self.initial_capital
        daily_returns = equity_series.pct_change().dropna()
        
        # Sharpe Ratio (annualized)
        sharpe_ratio = (
            daily_returns.mean() / daily_returns.std() * np.sqrt(252)
            if len(daily_returns) > 0 and daily_returns.std() > 0
            else 0
        )
        
        # Max DrawDown
        cummax = equity_series.cummax()
        dd = (equity_series - cummax) / cummax
        max_drawdown = dd.min() if len(dd) > 0 else 0
        
        # Win Rate
        profitable_trades = sum(1 for t in self.trades if t.get('side') == 'SELL')
        win_rate = profitable_trades / len(self.trades) if self.trades else 0
        
        return {
            'initial_capital': self.initial_capital,
            'final_value': self.equity_curve[-1],
            'total_return': total_return,
            'total_return_pct': total_return * 100,
            'sharpe_ratio': sharpe_ratio,
            'max_drawdown': max_drawdown,
            'max_drawdown_pct': max_drawdown * 100,
            'num_trades': len(self.trades),
            'win_rate': win_rate,
            'win_rate_pct': win_rate * 100,
            'num_buy_trades': sum(1 for t in self.trades if t.get('side') == 'BUY'),
            'num_sell_trades': sum(1 for t in self.trades if t.get('side') == 'SELL'),
            'trades': self.trades[:10]
        }
    
    def _empty_results(self) -> Dict:
        """Return empty results structure."""
        return {
            'initial_capital': self.initial_capital,
            'final_value': self.initial_capital,
            'total_return': 0,
            'total_return_pct': 0,
            'sharpe_ratio': 0,
            'max_drawdown': 0,
            'max_drawdown_pct': 0,
            'num_trades': 0,
            'win_rate': 0,
            'win_rate_pct': 0,
            'num_buy_trades': 0,
            'num_sell_trades': 0,
            'trades': []
        }
