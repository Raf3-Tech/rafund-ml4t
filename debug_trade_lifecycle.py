"""
DIAGNOSTIC: Inspect one complete trade lifecycle.

This script will:
1. Load sample price data
2. Generate a single trade
3. Show EVERY detail: entry z-score, position sizes, exit z-score, PnL
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

def create_sample_data():
    """Create synthetic price data for debugging."""
    print("\n" + "="*80)
    print("CREATING SAMPLE DATA FOR DIAGNOSTIC")
    print("="*80)
    
    # Create 180 days of correlated price data
    dates = pd.date_range(start='2024-01-01', periods=180, freq='D')
    
    # Symbol A: base trend with small random walk
    np.random.seed(42)
    price_a = 2000 + np.cumsum(np.random.normal(0, 10, 180))
    
    # Symbol B: highly correlated with A but lagging slightly
    price_b = 100 + np.cumsum(np.random.normal(0, 0.5, 180))
    price_b = price_b + (price_a - price_a.mean()) * 0.05  # correlation component
    
    data = []
    for i, date in enumerate(dates):
        data.append({'timestamp': date, 'symbol': 'BTC/USDT', 'close': float(price_a[i])})
        data.append({'timestamp': date, 'symbol': 'ETH/USDT', 'close': float(price_b[i])})
    
    df = pd.DataFrame(data)
    print(f"\nCreated {len(df)} records")
    print(f"BTC price range: ${price_a.min():.2f} - ${price_a.max():.2f}")
    print(f"ETH price range: ${price_b.min():.2f} - ${price_b.max():.2f}")
    
    return df

def calculate_spread_and_zscore(prices_df, lookback=60):
    """Calculate spread and z-score for a pairs trade."""
    print("\n" + "="*80)
    print("CALCULATING SPREAD & Z-SCORE")
    print("="*80)
    
    btc = prices_df[prices_df['symbol'] == 'BTC/USDT'].reset_index(drop=True)
    eth = prices_df[prices_df['symbol'] == 'ETH/USDT'].reset_index(drop=True)
    
    # Ensure same timestamps
    prices_pivot = prices_df.pivot_table(
        index='timestamp', 
        columns='symbol', 
        values='close'
    ).reset_index()
    
    print(f"\nData shape: {prices_pivot.shape}")
    print(f"Date range: {prices_pivot['timestamp'].min()} to {prices_pivot['timestamp'].max()}")
    
    # Calculate hedge ratio (simple: ratio of prices)
    log_btc = np.log(prices_pivot['BTC/USDT'])
    log_eth = np.log(prices_pivot['ETH/USDT'])
    
    # Simple hedge ratio (OLS would be better, but this is illustrative)
    hedge_ratio = prices_pivot['BTC/USDT'].mean() / prices_pivot['ETH/USDT'].mean()
    print(f"\nHedge ratio (BTC/ETH): {hedge_ratio:.4f}")
    
    # Calculate spread
    spread = log_btc - hedge_ratio * log_eth
    
    # Calculate z-score
    mean = spread.rolling(lookback).mean()
    std = spread.rolling(lookback).std()
    z_score = (spread - mean) / std
    
    prices_pivot['spread'] = spread
    prices_pivot['z_score'] = z_score
    prices_pivot['ma'] = mean
    prices_pivot['std'] = std
    
    return prices_pivot

def find_first_entry(df, entry_threshold=2.0):
    """Find the first trade entry."""
    print("\n" + "="*80)
    print("SEARCHING FOR FIRST ENTRY SIGNAL")
    print("="*80)
    
    # Skip NaN values (first lookback period)
    valid = df[df['z_score'].notna()].copy()
    
    # Look for entry: z-score > entry_threshold
    long_entries = valid[valid['z_score'] > entry_threshold]
    short_entries = valid[valid['z_score'] < -entry_threshold]
    
    print(f"\nTotal valid z-scores: {len(valid)}")
    print(f"Long entry signals (z > {entry_threshold}): {len(long_entries)}")
    print(f"Short entry signals (z < {-entry_threshold}): {len(short_entries)}")
    
    if len(long_entries) > 0:
        entry_row = long_entries.iloc[0]
        signal_type = "LONG SPREAD (buy BTC, short ETH)"
    elif len(short_entries) > 0:
        entry_row = short_entries.iloc[0]
        signal_type = "SHORT SPREAD (sell BTC, buy ETH)"
    else:
        print("\n❌ NO ENTRY SIGNALS FOUND IN DATA")
        return None, None
    
    print(f"\n✅ FIRST ENTRY FOUND")
    print(f"\nSignal type: {signal_type}")
    return entry_row, signal_type

def simulate_trade(df, entry_idx, entry_row, signal_type, exit_threshold=0.5, initial_capital=100000, max_position_pct=0.10):
    """Simulate one complete trade from entry to exit."""
    print("\n" + "="*80)
    print("SIMULATING TRADE LIFECYCLE")
    print("="*80)
    
    entry_date = entry_row['timestamp']
    entry_z = entry_row['z_score']
    btc_price_entry = entry_row['BTC/USDT']
    eth_price_entry = entry_row['ETH/USDT']
    
    print(f"\n📍 ENTRY")
    print(f"  Date:           {entry_date.strftime('%Y-%m-%d')}")
    print(f"  Z-score:        {entry_z:.4f}")
    print(f"  BTC price:      ${btc_price_entry:.2f}")
    print(f"  ETH price:      ${eth_price_entry:.2f}")
    
    # Calculate position sizes for dollar neutrality
    capital_per_leg = initial_capital * max_position_pct
    
    if "buy BTC, short ETH" in signal_type:
        # Long BTC, Short ETH
        qty_btc = capital_per_leg / btc_price_entry
        qty_eth = capital_per_leg / eth_price_entry
        position = "LONG BTC + SHORT ETH"
    else:
        # Short BTC, Long ETH
        qty_btc = -(capital_per_leg / btc_price_entry)
        qty_eth = capital_per_leg / eth_price_entry
        position = "SHORT BTC + LONG ETH"
    
    print(f"  Position:       {position}")
    print(f"  Capital allocated: ${capital_per_leg:.2f} per leg (total ${capital_per_leg*2:.2f})")
    print(f"  BTC quantity:   {qty_btc:.4f}")
    print(f"  ETH quantity:   {qty_eth:.4f}")
    print(f"  Notional BTC:   ${abs(qty_btc) * btc_price_entry:.2f}")
    print(f"  Notional ETH:   ${abs(qty_eth) * eth_price_entry:.2f}")
    
    # Find exit
    future_data = df.iloc[entry_idx+1:].copy()
    future_data['abs_z'] = future_data['z_score'].abs()
    
    # Exit when z-score reverts to threshold
    exits = future_data[
        (future_data['z_score'].abs() <= exit_threshold) & 
        (future_data['z_score'].notna())
    ]
    
    if len(exits) == 0:
        print(f"\n ❌ NO EXIT SIGNAL FOUND (trade still open)")
        return None
    
    exit_row = exits.iloc[0]
    exit_idx = entry_idx + len(future_data) - len(future_data[future_data.index >= exit_row.name])
    
    exit_date = exit_row['timestamp']
    exit_z = exit_row['z_score']
    btc_price_exit = exit_row['BTC/USDT']
    eth_price_exit = exit_row['ETH/USDT']
    
    print(f"\n📍 EXIT")
    print(f"  Date:           {exit_date.strftime('%Y-%m-%d')}")
    print(f"  Z-score:        {exit_z:.4f}")
    print(f"  BTC price:      ${btc_price_exit:.2f}")
    print(f"  ETH price:      ${eth_price_exit:.2f}")
    print(f"  Duration:       {(exit_date - entry_date).days} days")
    
    # Calculate PnL
    commission_rate = 0.001
    
    if "buy BTC, short ETH" in signal_type:
        # Long BTC
        btc_pnl = qty_btc * (btc_price_exit - btc_price_entry) * (1 - commission_rate)
        # Short ETH
        eth_pnl = qty_eth * (eth_price_entry - eth_price_exit) * (1 - commission_rate)
    else:
        # Short BTC
        btc_pnl = qty_btc * (btc_price_entry - btc_price_exit) * (1 - commission_rate)
        # Long ETH
        eth_pnl = qty_eth * (eth_price_exit - eth_price_entry) * (1 - commission_rate)
    
    total_pnl = btc_pnl + eth_pnl
    
    print(f"\n💰 PROFIT/LOSS CALCULATION")
    print(f"  BTC PnL:        ${btc_pnl:,.2f}")
    print(f"  ETH PnL:        ${eth_pnl:,.2f}")
    print(f"  Total PnL:      ${total_pnl:,.2f}")
    print(f"  Return:         {(total_pnl / (capital_per_leg*2)) * 100:.2f}%")
    
    print(f"\n📊 SPREAD DYNAMICS")
    print(f"  Entry spread:   {entry_row['spread']:.6f}")
    print(f"  Exit spread:    {exit_row['spread']:.6f}")
    print(f"  Entry z-score momentum: 2.0+ sigma territory")
    print(f"  Exit z-score reversion: back to ~0")
    print(f"  ✓ Mean reversion confirmed")

if __name__ == '__main__':
    # Generate sample data
    prices = create_sample_data()
    
    # Calculate spread and z-score
    analysis_df = calculate_spread_and_zscore(prices, lookback=60)
    
    # Find first entry
    entry_row, signal_type = find_first_entry(analysis_df, entry_threshold=2.0)
    
    if entry_row is None:
        print("\n❌ Cannot debug: no entry signals in data")
    else:
        entry_idx = analysis_df[analysis_df['timestamp'] == entry_row['timestamp']].index[0]
        # Simulate the trade
        simulate_trade(analysis_df, entry_idx, entry_row, signal_type, exit_threshold=0.5)
    
    print("\n" + "="*80)
    print("DIAGNOSTIC COMPLETE")
    print("="*80)
