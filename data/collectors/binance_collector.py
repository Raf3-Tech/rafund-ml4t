"""
Binance data collector using CCXT.

This module fetches OHLCV (Open, High, Low, Close, Volume) data from Binance
and handles rate limiting, error management, and data validation.
"""

import ccxt
import pandas as pd
import logging
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


class BinanceCollector:
    """Fetches market data from Binance exchange."""
    
    def __init__(self, testnet: bool = False, rate_limit_ms: int = 50):
        """
        Initialize Binance collector.
        
        Args:
            testnet: Use testnet (False = mainnet)
            rate_limit_ms: Milliseconds to wait between API calls
        """
        self.testnet = testnet
        self.rate_limit_ms = rate_limit_ms
        self.client = None
        self.last_call_time = 0
        self._initialize_client()
        
    def _initialize_client(self):
        """Initialize CCXT Binance client."""
        try:
            self.client = ccxt.binance({
                'enableRateLimit': True,
                'rateLimit': self.rate_limit_ms,
                'sandbox': self.testnet
            })
            logger.info(f"Binance client initialized (testnet={self.testnet})")
        except Exception as e:
            logger.error(f"Failed to initialize Binance client: {str(e)}")
            raise
    
    def _respect_rate_limit(self):
        """Respect rate limiting between API calls."""
        elapsed = time.time() - self.last_call_time
        wait_time = max(0, (self.rate_limit_ms / 1000) - elapsed)
        if wait_time > 0:
            time.sleep(wait_time)
        self.last_call_time = time.time()
    
    def get_symbols(self) -> List[str]:
        """
        Get all available trading symbols on Binance.
        
        Returns:
            List of symbols in format 'BTC/USDT'
        """
        try:
            self._respect_rate_limit()
            # Load markets first to populate symbols
            if not self.client.symbols:
                self.client.load_markets()
            symbols = self.client.symbols
            if symbols:
                logger.info(f"Retrieved {len(symbols)} symbols from Binance")
                return symbols
            else:
                logger.warning("No symbols retrieved from Binance")
                return []
        except Exception as e:
            logger.error(f"Error fetching symbols: {str(e)}")
            return []
    
    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = '1d',
        limit: int = 100,
        since: Optional[int] = None
    ) -> pd.DataFrame:
        """
        Fetch OHLCV data for a symbol.
        
        Args:
            symbol: Trading pair (e.g., 'BTC/USDT')
            timeframe: Candle timeframe ('1m', '5m', '15m', '1h', '4h', '1d', '1w')
            limit: Number of candles to fetch (max 1000 per call)
            since: Unix timestamp in milliseconds (for pagination)
            
        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume
        """
        try:
            self._respect_rate_limit()
            
            logger.info(f"Fetching {limit} {timeframe} candles for {symbol}")
            ohlcv = self.client.fetch_ohlcv(symbol, timeframe, since, limit)
            
            if not ohlcv:
                logger.warning(f"No data returned for {symbol}")
                return pd.DataFrame()
            
            # Convert to DataFrame
            df = pd.DataFrame(
                ohlcv,
                columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
            )
            
            # Convert timestamp from milliseconds to datetime
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df['symbol'] = symbol
            
            logger.info(f"Successfully fetched {len(df)} candles for {symbol}")
            return df
            
        except ccxt.NetworkError as e:
            logger.error(f"Network error fetching {symbol}: {str(e)}")
            return pd.DataFrame()
        except ccxt.ExchangeError as e:
            logger.error(f"Exchange error fetching {symbol}: {str(e)}")
            return pd.DataFrame()
        except Exception as e:
            logger.error(f"Unexpected error fetching {symbol}: {str(e)}")
            return pd.DataFrame()
    
    def fetch_ohlcv_history(
        self,
        symbol: str,
        timeframe: str = '1d',
        start_date: datetime = None,
        end_date: datetime = None
    ) -> pd.DataFrame:
        """
        Fetch historical OHLCV data for extended periods.
        
        Uses pagination to fetch data across the entire time range.
        Binance API limit is 1000 candles per call.
        
        Args:
            symbol: Trading pair
            timeframe: Candle timeframe
            start_date: Start date (default: 1 year ago)
            end_date: End date (default: today)
            
        Returns:
            DataFrame with all historical data
        """
        if start_date is None:
            start_date = datetime.utcnow() - timedelta(days=365)
        if end_date is None:
            end_date = datetime.utcnow()
        
        logger.info(f"Fetching {symbol} history from {start_date} to {end_date}")
        
        all_data = []
        current_time = int(start_date.timestamp() * 1000)  # Convert to milliseconds
        end_time = int(end_date.timestamp() * 1000)
        
        # Determine candle duration in milliseconds
        timeframe_ms = {
            '1m': 60 * 1000,
            '5m': 5 * 60 * 1000,
            '15m': 15 * 60 * 1000,
            '1h': 60 * 60 * 1000,
            '4h': 4 * 60 * 60 * 1000,
            '1d': 24 * 60 * 60 * 1000,
            '1w': 7 * 24 * 60 * 60 * 1000
        }
        
        candle_duration = timeframe_ms.get(timeframe, 24 * 60 * 60 * 1000)
        
        while current_time < end_time:
            try:
                df = self.fetch_ohlcv(symbol, timeframe, 1000, current_time)
                
                if df.empty:
                    logger.warning(f"No data for {symbol} at {datetime.fromtimestamp(current_time/1000)}")
                    break
                
                all_data.append(df)
                
                # Move to next batch (1000 candles)
                current_time = int(df['timestamp'].iloc[-1].timestamp() * 1000) + candle_duration
                
                # Log progress
                logger.info(f"Fetched up to {df['timestamp'].iloc[-1].date()} for {symbol}")
                
            except Exception as e:
                logger.error(f"Error during history fetch: {str(e)}")
                break
        
        if all_data:
            result = pd.concat(all_data, ignore_index=True)
            result = result.drop_duplicates(subset=['symbol', 'timestamp']).sort_values('timestamp')
            logger.info(f"Total records fetched for {symbol}: {len(result)}")
            return result
        else:
            logger.warning(f"No data fetched for {symbol}")
            return pd.DataFrame()
    
    def validate_data(self, df: pd.DataFrame) -> bool:
        """
        Validate OHLCV data quality.
        
        Args:
            df: DataFrame with OHLCV data
            
        Returns:
            True if data is valid, False otherwise
        """
        if df.empty:
            logger.warning("Empty DataFrame provided for validation")
            return False
        
        required_columns = {'timestamp', 'open', 'high', 'low', 'close', 'volume', 'symbol'}
        if not required_columns.issubset(df.columns):
            logger.error(f"Missing required columns. Have: {df.columns.tolist()}")
            return False
        
        # Check for negative prices (data corruption)
        if (df[['open', 'high', 'low', 'close']] < 0).any().any():
            logger.error("Negative prices detected")
            return False
        
        # Check logical consistency: high >= low
        if (df['high'] < df['low']).any():
            logger.error("High < Low detected")
            return False
        
        # Check logical consistency: high >= close >= low
        if (df['high'] < df['close']).any() or (df['close'] < df['low']).any():
            logger.error("OHLC logical inconsistency detected")
            return False
        
        # Check for zero volume (can be valid but unusual)
        zero_volume = (df['volume'] == 0).sum()
        if zero_volume > 0:
            logger.warning(f"{zero_volume} candles with zero volume")
        
        logger.info("Data validation passed")
        return True
    
    def get_market_info(self, symbol: str) -> Dict:
        """
        Get market information for a symbol.
        
        Args:
            symbol: Trading pair
            
        Returns:
            Dictionary with market info (limits, precision, etc.)
        """
        try:
            self._respect_rate_limit()
            market = self.client.market(symbol)
            
            return {
                'symbol': symbol,
                'base': market.get('base'),
                'quote': market.get('quote'),
                'active': market.get('active'),
                'min_amount': market.get('limits', {}).get('amount', {}).get('min'),
                'max_amount': market.get('limits', {}).get('amount', {}).get('max'),
                'min_cost': market.get('limits', {}).get('cost', {}).get('min'),
            }
        except Exception as e:
            logger.error(f"Error fetching market info for {symbol}: {str(e)}")
            return {}
