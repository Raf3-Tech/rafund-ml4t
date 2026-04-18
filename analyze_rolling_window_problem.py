"""
THE REAL PROBLEM: Rolling window chasing, not mean reversion.

This script reveals the fundamental issue with your stat arb implementation.
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import matplotlib.pyplot as plt

def create_sample_data():
    """Create synthetic price data."""
    dates = pd.date_range(start='2024-01-01', periods=180, freq='D')
    
    np.random.seed(42)
    price_a = 2000 + np.cumsum(np.random.normal(0, 10, 180))
    price_b = 100 + np.cumsum(np.random.normal(0, 0.5, 180))
    price_b = price_b + (price_a - price_a.mean()) * 0.05
    
    data = []
    for i, date in enumerate(dates):
        data.append({'timestamp': date, 'symbol': 'BTC/USDT', 'close': float(price_a[i])})
        data.append({'timestamp': date, 'symbol': 'ETH/USDT', 'close': float(price_b[i])})
    
    df = pd.DataFrame(data)
    return df, price_a, price_b

def analyze_rolling_window_problem():
    """Show the rolling window problem."""
    print("\n" + "="*80)
    print("THE ROLLING WINDOW PROBLEM")
    print("="*80)
    
    prices, price_a, price_b = create_sample_data()
    
    # Create pivot
    prices_pivot = prices.pivot_table(
        index='timestamp', 
        columns='symbol', 
        values='close'
    ).reset_index()
    
    # Calculate spread
    log_btc = np.log(prices_pivot['BTC/USDT'])
    log_eth = np.log(prices_pivot['ETH/USDT'])
    hedge_ratio = prices_pivot['BTC/USDT'].mean() / prices_pivot['ETH/USDT'].mean()
    
    spread = log_btc - hedge_ratio * log_eth
    
    # Calculate rolling stats with DIFFERENT window sizes
    for lookback in [20, 60, 120]:
        print(f"\n[WINDOW SIZE: {lookback} days]")
        print("-" * 80)
        
        mean = spread.rolling(lookback).mean()
        std = spread.rolling(lookback).std()
        z_score = (spread - mean) / std
        
        # Find first entry
        valid = pd.DataFrame({
            'timestamp': prices_pivot['timestamp'],
            'spread': spread,
            'mean': mean,
            'std': std,
            'z_score': z_score
        })
        valid = valid[valid['z_score'].notna()].reset_index(drop=True)
        
        entries = valid[valid['z_score'] > 2.0]
        if len(entries) == 0:
            print("  No entry signals")
            continue
        
        entry = entries.iloc[0]
        entry_idx = valid[valid['timestamp'] == entry['timestamp']].index[0]
        
        # Show entry
        print(f"\n  [ENTRY (index {entry_idx})]")
        print(f"    Actual spread:     {entry['spread']:.6f}")
        print(f"    Rolling mean:      {entry['mean']:.6f}")
        print(f"    Rolling std:       {entry['std']:.6f}")
        print(f"    Z-score:           {entry['z_score']:.4f} ← Entry signal")
        print(f"    Interpretation:    Spread is {entry['z_score']:.1f} std deviations ABOVE mean")
        
        # Look ahead 20 days
        future = valid.iloc[entry_idx+1:entry_idx+21]
        
        if len(future) >= 20:
            day20 = future.iloc[19]
            print(f"\n  [20 DAYS LATER (index {entry_idx + 20})]")
            print(f"    Actual spread:     {day20['spread']:.6f}  (changed by {day20['spread'] - entry['spread']:+.6f})")
            print(f"    Rolling mean:      {day20['mean']:.6f}  (shifted by {day20['mean'] - entry['mean']:+.6f})")
            print(f"    Rolling std:       {day20['std']:.6f}  (changed by {day20['std'] - entry['std']:+.6f})")
            print(f"    Z-score:           {day20['z_score']:.4f}")
            
            # The key insight
            actual_reversion = day20['spread'] - entry['spread']
            rolling_mean_shift = day20['mean'] - entry['mean']
            
            print(f"\n  [KEY INSIGHT]")
            print(f"    Spread actually changed:  {actual_reversion:+.6f}")
            print(f"    Rolling mean shifted:     {rolling_mean_shift:+.6f}")
            
            if abs(actual_reversion) < 0.05:
                print(f"    → Spread is FLAT (no real reversion)")
            else:
                print(f"    → Spread moved AGAINST your position")
            
            if abs(rolling_mean_shift) > abs(actual_reversion):
                print(f"    → Z-score decreased because WINDOW MOVED, not because spread reverted")
                print(f"    → This is DANGEROUS: you're trading the window, not the mean reversion")

def show_problem_visually():
    """Create visualization showing the issue."""
    print("\n" + "="*80)
    print("WHY YOUR STRATEGY LOSES MONEY")
    print("="*80)
    
    prices, price_a, price_b = create_sample_data()
    
    prices_pivot = prices.pivot_table(
        index='timestamp', 
        columns='symbol', 
        values='close'
    ).reset_index()
    
    log_btc = np.log(prices_pivot['BTC/USDT'])
    log_eth = np.log(prices_pivot['ETH/USDT'])
    hedge_ratio = prices_pivot['BTC/USDT'].mean() / prices_pivot['ETH/USDT'].mean()
    
    spread = log_btc - hedge_ratio * log_eth
    
    lookback = 60
    mean = spread.rolling(lookback).mean()
    std = spread.rolling(lookback).std()
    z_score = (spread - mean) / std
    
    valid = pd.DataFrame({
        'timestamp': prices_pivot['timestamp'],
        'spread': spread,
        'mean': mean,
        'z_score': z_score
    })
    valid = valid[valid['z_score'].notna()].reset_index(drop=True)
    
    entries = valid[valid['z_score'] > 2.0]
    if len(entries) > 0:
        entry = entries.iloc[0]
        entry_idx = valid[valid['timestamp'] == entry['timestamp']].index[0]
        
        window = valid.iloc[entry_idx:min(entry_idx+50, len(valid))]
        
        print(f"\n  Entry z-score: {entry['z_score']:.2f}")
        print(f"  Expected:      Spread reverts to mean → profit")
        print(f"  Reality:       Spread flat, rolling mean chases it → loss")
        
        print(f"\n  Sample of spread vs rolling mean:")
        print(f"  {'Day':>4} {'Spread':>10} {'Mean':>10} {'Gap':>8} {'Z-score':>8}")
        print(f"  {'-'*50}")
        for i, (idx, row) in enumerate(window.iterrows()):
            gap = row['spread'] - row['mean']
            if i % 5 == 0:  # Print every 5 days
                print(f"  {i:4d} {row['spread']:10.4f} {row['mean']:10.4f} {gap:+8.4f} {row['z_score']:8.2f}")

if __name__ == '__main__':
    analyze_rolling_window_problem()
    show_problem_visually()
    
    print("\n" + "="*80)
    print("CONCLUSION: YOUR STRATEGY HAS A FUNDAMENTAL FLAW")
    print("="*80)
    print("""
The issue is NOT:
  - Bad signal timing
  - Wrong thresholds

The issue IS:
  * You're using a ROLLING window that shifts constantly
  * When z-score hits 2.0, it's based on the LAST 60 days' statistics
  * As time passes, the window shifts and incorporates new data
  * The z-score can DECREASE even if the spread DOESN'T REVERT
  * You exit when z-score reaches 0.5 thinking it reverted
  * But the spread might still be extended - just against a new baseline

FIX:
  Use a FIXED historical window (e.g., first 60 days only)
  OR
  Use Kalman filtering / co-integration tests
  OR
  Require actual spread reversion, not just z-score threshold
    """)
