"""
PostgreSQL database connection and operations.

This module handles all database interactions including
connection pooling, data insertion, and querying.
"""

import os
import psycopg2
from psycopg2.pool import SimpleConnectionPool
from psycopg2.extras import execute_values
import pandas as pd
import logging
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from pathlib import Path

# Load environment variables from .env file
def load_env():
    """Load .env file manually."""
    env_path = Path(__file__).parent.parent / '.env'
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    key, value = line.split('=', 1)
                    os.environ[key.strip()] = value.strip()

load_env()

logger = logging.getLogger(__name__)


class DatabaseConnection:
    """PostgreSQL database connection manager."""
    
    def __init__(
        self,
        host: str = None,
        port: int = None,
        database: str = None,
        user: str = None,
        password: str = None,
        min_conn: int = 1,
        max_conn: int = 5
    ):
        """
        Initialize database connection with connection pooling.
        
        Args:
            host: PostgreSQL host
            port: PostgreSQL port
            database: Database name
            user: Database user
            password: Database password
            min_conn: Minimum connections in pool
            max_conn: Maximum connections in pool
        """
        self.host = host or os.getenv('DB_HOST', 'localhost')
        self.port = int(port or os.getenv('DB_PORT', 5432))
        self.database = database or os.getenv('DB_NAME', 'rafund')
        self.user = user or os.getenv('DB_USER', 'postgres')
        self.password = password or os.getenv('DB_PASSWORD', 'postgres')
        self.min_conn = min_conn
        self.max_conn = max_conn
        self.pool = None
        self._initialize_pool()
    
    def _initialize_pool(self):
        """Initialize connection pool."""
        try:
            self.pool = SimpleConnectionPool(
                self.min_conn,
                self.max_conn,
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.user,
                password=self.password
            )
            logger.info(f"Database connection pool initialized for {self.database}")
        except Exception as e:
            logger.error(f"Failed to initialize connection pool: {str(e)}")
            raise
    
    def get_connection(self):
        """Get a connection from the pool."""
        try:
            conn = self.pool.getconn()
            return conn
        except Exception as e:
            logger.error(f"Error getting connection from pool: {str(e)}")
            raise
    
    def return_connection(self, conn):
        """Return connection to the pool."""
        try:
            self.pool.putconn(conn)
        except Exception as e:
            logger.error(f"Error returning connection: {str(e)}")
    
    def test_connection(self) -> bool:
        """Test database connection."""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.close()
            self.return_connection(conn)
            logger.info("Database connection test passed")
            return True
        except Exception as e:
            logger.error(f"Database connection test failed: {str(e)}")
            return False
    
    def insert_prices(self, df: pd.DataFrame) -> int:
        """
        Insert OHLCV data into prices table.
        
        Args:
            df: DataFrame with columns: timestamp, open, high, low, close, volume, symbol
            
        Returns:
            Number of rows inserted
        """
        if df.empty:
            logger.warning("Empty DataFrame provided to insert_prices")
            return 0
        
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            # Prepare data for insertion
            data = []
            for _, row in df.iterrows():
                data.append((
                    row['symbol'],
                    row['timestamp'],
                    float(row['open']),
                    float(row['high']),
                    float(row['low']),
                    float(row['close']),
                    float(row['volume'])
                ))
            
            # Insert data
            query = """
                INSERT INTO prices (symbol, timestamp, open, high, low, close, volume)
                VALUES %s
                ON CONFLICT (symbol, timestamp) DO NOTHING
            """
            
            execute_values(cursor, query, data)
            conn.commit()
            
            inserted = cursor.rowcount
            logger.info(f"Inserted {inserted} price records into database")
            
            cursor.close()
            self.return_connection(conn)
            
            return inserted
            
        except Exception as e:
            logger.error(f"Error inserting prices: {str(e)}")
            conn.rollback()
            cursor.close()
            self.return_connection(conn)
            return 0
    
    def get_prices(
        self,
        symbol: str,
        start_date: datetime = None,
        end_date: datetime = None
    ) -> pd.DataFrame:
        """
        Retrieve price data from database.
        
        Args:
            symbol: Trading symbol
            start_date: Start date (optional)
            end_date: End date (optional)
            
        Returns:
            DataFrame with price data
        """
        try:
            conn = self.get_connection()
            
            query = "SELECT * FROM prices WHERE symbol = %s"
            params = [symbol]
            
            if start_date:
                query += " AND timestamp >= %s"
                params.append(start_date)
            
            if end_date:
                query += " AND timestamp <= %s"
                params.append(end_date)
            
            query += " ORDER BY timestamp"
            
            df = pd.read_sql(query, conn, params=params)
            
            self.return_connection(conn)
            
            logger.info(f"Retrieved {len(df)} price records for {symbol}")
            return df
            
        except Exception as e:
            logger.error(f"Error retrieving prices: {str(e)}")
            self.return_connection(conn)
            return pd.DataFrame()
    
    def get_latest_timestamp(self, symbol: str) -> Optional[datetime]:
        """
        Get the latest timestamp for a symbol.
        
        Args:
            symbol: Trading symbol
            
        Returns:
            Latest timestamp or None if no data
        """
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                "SELECT MAX(timestamp) FROM prices WHERE symbol = %s",
                (symbol,)
            )
            
            result = cursor.fetchone()
            cursor.close()
            self.return_connection(conn)
            
            return result[0] if result[0] else None
            
        except Exception as e:
            logger.error(f"Error getting latest timestamp: {str(e)}")
            self.return_connection(conn)
            return None
    
    def get_symbols_with_data(self) -> List[str]:
        """
        Get all symbols that have data in the database.
        
        Returns:
            List of symbols
        """
        try:
            conn = self.get_connection()
            
            query = "SELECT DISTINCT symbol FROM prices ORDER BY symbol"
            df = pd.read_sql(query, conn)
            
            self.return_connection(conn)
            
            symbols = df['symbol'].tolist()
            logger.info(f"Found {len(symbols)} symbols in database")
            return symbols
            
        except Exception as e:
            logger.error(f"Error getting symbols: {str(e)}")
            self.return_connection(conn)
            return []
    
    def insert_features(self, df: pd.DataFrame) -> int:
        """
        Insert calculated features into features table.
        
        Args:
            df: DataFrame with columns: symbol_a, symbol_b, timestamp, spread, 
                spread_mean, spread_std, z_score, hedge_ratio
                
        Returns:
            Number of rows inserted
        """
        if df.empty:
            logger.warning("Empty DataFrame provided to insert_features")
            return 0
        
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            data = []
            for _, row in df.iterrows():
                data.append((
                    row['symbol_a'],
                    row['symbol_b'],
                    row['timestamp'],
                    float(row.get('spread')),
                    float(row.get('spread_mean')),
                    float(row.get('spread_std')),
                    float(row.get('z_score')),
                    float(row.get('hedge_ratio'))
                ))
            
            query = """
                INSERT INTO features (symbol_a, symbol_b, timestamp, spread, 
                                     spread_mean, spread_std, z_score, hedge_ratio)
                VALUES %s
                ON CONFLICT (symbol_a, symbol_b, timestamp) DO NOTHING
            """
            
            execute_values(cursor, query, data)
            conn.commit()
            
            inserted = cursor.rowcount
            logger.info(f"Inserted {inserted} feature records into database")
            
            cursor.close()
            self.return_connection(conn)
            
            return inserted
            
        except Exception as e:
            logger.error(f"Error inserting features: {str(e)}")
            conn.rollback()
            cursor.close()
            self.return_connection(conn)
            return 0
    
    def insert_signals(self, df: pd.DataFrame) -> int:
        """
        Insert trading signals into signals table.
        
        Args:
            df: DataFrame with columns: symbol_a, symbol_b, timestamp, signal, 
                z_score, position_a, position_b
                
        Returns:
            Number of rows inserted
        """
        if df.empty:
            logger.warning("Empty DataFrame provided to insert_signals")
            return 0
        
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            data = []
            for _, row in df.iterrows():
                data.append((
                    row['symbol_a'],
                    row['symbol_b'],
                    row['timestamp'],
                    row['signal'],
                    float(row.get('z_score')),
                    int(row.get('position_a', 0)),
                    int(row.get('position_b', 0))
                ))
            
            query = """
                INSERT INTO signals (symbol_a, symbol_b, timestamp, signal, 
                                    z_score, position_a, position_b)
                VALUES %s
            """
            
            execute_values(cursor, query, data)
            conn.commit()
            
            inserted = cursor.rowcount
            logger.info(f"Inserted {inserted} signal records into database")
            
            cursor.close()
            self.return_connection(conn)
            
            return inserted
            
        except Exception as e:
            logger.error(f"Error inserting signals: {str(e)}")
            conn.rollback()
            cursor.close()
            self.return_connection(conn)
            return 0
    
    def get_data_stats(self) -> Dict:
        """
        Get statistics about data in the database.
        
        Returns:
            Dictionary with counts and date ranges
        """
        try:
            conn = self.get_connection()
            
            stats = {}
            cursor = conn.cursor()
            
            # Count records
            cursor.execute("SELECT COUNT(*) FROM prices")
            stats['total_price_records'] = cursor.fetchone()[0]
            
            # Get date range
            cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM prices")
            min_date, max_date = cursor.fetchone()
            stats['min_date'] = min_date
            stats['max_date'] = max_date
            
            # Count symbols
            cursor.execute("SELECT COUNT(DISTINCT symbol) FROM prices")
            stats['num_symbols'] = cursor.fetchone()[0]
            
            cursor.close()
            self.return_connection(conn)
            
            logger.info(f"Database stats: {stats['total_price_records']} records, "
                       f"{stats['num_symbols']} symbols")
            
            return stats
            
        except Exception as e:
            logger.error(f"Error getting data stats: {str(e)}")
            self.return_connection(conn)
            return {}
    
    def insert_backtest_results(self, results: Dict) -> bool:
        """
        Insert backtest results into backtest_results table.
        
        Args:
            results: Dictionary with backtest results including:
                    backtest_id, start_date, end_date, initial_capital,
                    final_value, total_return, sharpe_ratio, max_drawdown,
                    num_trades, win_rate
                    
        Returns:
            True if successful, False otherwise
        """
        if not results:
            logger.warning("Empty results provided to insert_backtest_results")
            return False
        
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            query = """
                INSERT INTO backtest_results 
                (backtest_id, start_date, end_date, initial_capital, final_value, 
                 total_return, sharpe_ratio, max_drawdown, num_trades, win_rate, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (backtest_id) DO UPDATE SET
                    final_value = EXCLUDED.final_value,
                    total_return = EXCLUDED.total_return,
                    sharpe_ratio = EXCLUDED.sharpe_ratio,
                    max_drawdown = EXCLUDED.max_drawdown,
                    num_trades = EXCLUDED.num_trades,
                    win_rate = EXCLUDED.win_rate
            """
            
            cursor.execute(query, (
                results.get('backtest_id'),
                results.get('start_date'),
                results.get('end_date'),
                float(results.get('initial_capital', 0)),
                float(results.get('final_value', 0)),
                float(results.get('total_return', 0)),
                float(results.get('sharpe_ratio', 0)),
                float(results.get('max_drawdown', 0)),
                int(results.get('num_trades', 0)),
                float(results.get('win_rate', 0))
            ))
            
            conn.commit()
            cursor.close()
            self.return_connection(conn)
            
            logger.info(f"Saved backtest results for {results.get('backtest_id')}")
            return True
            
        except Exception as e:
            logger.error(f"Error inserting backtest results: {str(e)}")
            try:
                conn.rollback()
                cursor.close()
                self.return_connection(conn)
            except:
                pass
            return False
    
    def insert_trades(self, df: pd.DataFrame) -> int:
        """
        Insert trade records into trades table.
        
        Args:
            df: DataFrame with columns: symbol, trade_date, entry_price, exit_price,
                quantity, direction, pnl, return_pct, status
                
        Returns:
            Number of rows inserted
        """
        if df.empty:
            logger.warning("Empty DataFrame provided to insert_trades")
            return 0
        
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            data = []
            for _, row in df.iterrows():
                data.append((
                    row['symbol'],
                    row['trade_date'],
                    float(row.get('entry_price')),
                    float(row.get('exit_price')) if pd.notna(row.get('exit_price')) else None,
                    int(row.get('quantity')),
                    row['direction'],
                    float(row.get('pnl')) if pd.notna(row.get('pnl')) else None,
                    float(row.get('return_pct')) if pd.notna(row.get('return_pct')) else None,
                    row.get('status', 'OPEN')
                ))
            
            query = """
                INSERT INTO trades (symbol, trade_date, entry_price, exit_price, 
                                   quantity, direction, pnl, return_pct, status)
                VALUES %s
            """
            
            execute_values(cursor, query, data)
            conn.commit()
            
            inserted = cursor.rowcount
            logger.info(f"Inserted {inserted} trade records into database")
            
            cursor.close()
            self.return_connection(conn)
            
            return inserted
            
        except Exception as e:
            logger.error(f"Error inserting trades: {str(e)}")
            try:
                conn.rollback()
                cursor.close()
                self.return_connection(conn)
            except:
                pass
            return 0
    
    def close_pool(self):
        """Close all connections in the pool."""
        try:
            self.pool.closeall()
            logger.info("Connection pool closed")
        except Exception as e:
            logger.error(f"Error closing connection pool: {str(e)}")
