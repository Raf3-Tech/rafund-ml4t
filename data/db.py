"""
PostgreSQL database connection and operations.

This module handles all database interactions including
connection pooling, data insertion, and querying.
"""

import json
import logging
import os
import psycopg2
from psycopg2.pool import SimpleConnectionPool
from psycopg2.extras import execute_values
import pandas as pd
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path

from config.logging_config import get_logger

logger = get_logger(__name__)

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
        database_url: str = None,
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
            database_url: Optional PostgreSQL DSN URL
            min_conn: Minimum connections in pool
            max_conn: Maximum connections in pool
        """
        self.database_url = database_url or os.getenv('DATABASE_URL')
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
            pool_config = {
                'dsn': self.database_url
            } if self.database_url else {
                'host': self.host,
                'port': self.port,
                'database': self.database,
                'user': self.user,
                'password': self.password,
            }

            self.pool = SimpleConnectionPool(
                self.min_conn,
                self.max_conn,
                **pool_config
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

    def get_feature_pairs(self) -> List[tuple]:
        """
        Get all unique stationary feature pairs from the features table.
        
        Returns:
            List of (symbol_a, symbol_b) tuples
        """
        try:
            conn = self.get_connection()
            query = "SELECT DISTINCT symbol_a, symbol_b FROM features ORDER BY symbol_a, symbol_b"
            df = pd.read_sql(query, conn)
            self.return_connection(conn)
            pairs = [(row['symbol_a'], row['symbol_b']) for _, row in df.iterrows()]
            logger.info(f"Found {len(pairs)} feature pairs in database")
            return pairs
        except Exception as e:
            logger.error(f"Error getting feature pairs: {str(e)}")
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
            
            inserted = len(data)
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
            
            inserted = len(data)
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
    
    def insert_portfolio(self, df: pd.DataFrame) -> int:
        """
        Insert portfolio snapshots into portfolio table.

        Expected columns: timestamp, symbol, position_size, entry_price,
        current_price, unrealized_pnl
        """
        if df.empty:
            return 0
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            data = [
                (
                    row["timestamp"],
                    row["symbol"],
                    int(row["position_size"]),
                    float(row["entry_price"]),
                    float(row["current_price"]) if pd.notna(row.get("current_price")) else None,
                    float(row["unrealized_pnl"]) if pd.notna(row.get("unrealized_pnl")) else None,
                )
                for _, row in df.iterrows()
            ]
            query = """
                INSERT INTO portfolio (timestamp, symbol, position_size, entry_price,
                                       current_price, unrealized_pnl)
                VALUES %s
                ON CONFLICT (timestamp, symbol) DO UPDATE SET
                    position_size = EXCLUDED.position_size,
                    entry_price = EXCLUDED.entry_price,
                    current_price = EXCLUDED.current_price,
                    unrealized_pnl = EXCLUDED.unrealized_pnl
            """
            execute_values(cursor, query, data)
            conn.commit()
            inserted = cursor.rowcount
            cursor.close()
            self.return_connection(conn)
            logger.info(f"Upserted {inserted} portfolio snapshots")
            return inserted
        except Exception as e:
            logger.error(f"Error inserting portfolio: {str(e)}")
            try:
                conn.rollback()
                cursor.close()
                self.return_connection(conn)
            except Exception:
                pass
            return 0

    def delete_signals_for_pair(self, symbol_a: str, symbol_b: str) -> int:
        """Remove signals for a pair before re-inserting."""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM signals WHERE symbol_a = %s AND symbol_b = %s",
                (symbol_a, symbol_b),
            )
            deleted = cursor.rowcount
            conn.commit()
            cursor.close()
            self.return_connection(conn)
            logger.info(f"Deleted {deleted} signals for {symbol_a}/{symbol_b}")
            return deleted
        except Exception as e:
            logger.error(f"Error deleting signals: {str(e)}")
            return 0

    def delete_features_for_pair(self, symbol_a: str, symbol_b: str) -> int:
        """Remove features for a pair before re-calculating."""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM features WHERE symbol_a = %s AND symbol_b = %s",
                (symbol_a, symbol_b),
            )
            deleted = cursor.rowcount
            conn.commit()
            cursor.close()
            self.return_connection(conn)
            logger.info(f"Deleted {deleted} features for {symbol_a}/{symbol_b}")
            return deleted
        except Exception as e:
            logger.error(f"Error deleting features: {str(e)}")
            return 0

    def delete_trades_for_symbol(self, symbol: str) -> int:
        """Remove trades for a spread symbol label before re-inserting."""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM trades WHERE symbol = %s", (symbol,))
            deleted = cursor.rowcount
            conn.commit()
            cursor.close()
            self.return_connection(conn)
            return deleted
        except Exception as e:
            logger.error(f"Error deleting trades: {str(e)}")
            return 0

    def delete_portfolio_for_symbol(self, symbol: str) -> int:
        """Remove portfolio rows for a symbol label."""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM portfolio WHERE symbol = %s", (symbol,))
            deleted = cursor.rowcount
            conn.commit()
            cursor.close()
            self.return_connection(conn)
            return deleted
        except Exception as e:
            logger.error(f"Error deleting portfolio: {str(e)}")
            return 0

    def get_table_counts(self) -> Dict[str, int]:
        """Row counts for all core tables."""
        tables = ["prices", "features", "signals", "trades", "portfolio", "backtest_results"]
        counts = {}
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            for table in tables:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                counts[table] = cursor.fetchone()[0]
            cursor.close()
            self.return_connection(conn)
            return counts
        except Exception as e:
            logger.error(f"Error getting table counts: {str(e)}")
            return counts

    def get_active_production_model(self, model_name: str, symbol: str) -> Optional[Dict[str, Any]]:
        try:
            conn = self.get_connection()
            query = (
                "SELECT * FROM model_registry "
                "WHERE model_name = %s AND symbol = %s AND stage = 'Production' AND is_active = TRUE "
                "ORDER BY promoted_at DESC LIMIT 1"
            )
            df = pd.read_sql(query, conn, params=[model_name, symbol])
            self.return_connection(conn)
            if df.empty:
                return None
            return df.iloc[0].to_dict()
        except Exception as e:
            logger.error(f"Error querying active production model: {str(e)}")
            self.return_connection(conn)
            return None

    def get_production_models(self) -> list[Dict[str, Any]]:
        try:
            conn = self.get_connection()
            query = "SELECT * FROM model_registry WHERE stage = 'Production' AND is_active = TRUE ORDER BY promoted_at DESC"
            df = pd.read_sql(query, conn)
            self.return_connection(conn)
            return df.to_dict(orient='records')
        except Exception as e:
            logger.error(f"Error querying production models: {str(e)}")
            self.return_connection(conn)
            return []

    def get_all_model_registry_entries(self) -> list[Dict[str, Any]]:
        try:
            conn = self.get_connection()
            query = "SELECT * FROM model_registry ORDER BY model_name, symbol, promoted_at DESC NULLS LAST"
            df = pd.read_sql(query, conn)
            self.return_connection(conn)
            return df.to_dict(orient='records')
        except Exception as e:
            logger.error(f"Error querying model registry entries: {str(e)}")
            self.return_connection(conn)
            return []

    def get_latest_drift_reports(self) -> list[Dict[str, Any]]:
        try:
            conn = self.get_connection()
            query = (
                "SELECT DISTINCT ON (model_name, symbol) model_name, symbol, severity, detected_at "
                "FROM drift_reports "
                "ORDER BY model_name, symbol, detected_at DESC"
            )
            df = pd.read_sql(query, conn)
            self.return_connection(conn)
            return df.to_dict(orient='records')
        except Exception as e:
            logger.error(f"Error querying latest drift reports: {str(e)}")
            self.return_connection(conn)
            return []

    def save_model_registry_entry(
        self,
        model_name: str,
        mlflow_run_id: str,
        mlflow_version: int,
        symbol: str,
        stage: str,
        trained_at: datetime,
        promoted_at: Optional[datetime],
        in_sample_sharpe: float,
        oos_sharpe: Optional[float],
        feature_names: list[str],
        is_active: bool,
    ) -> bool:
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            if is_active:
                cursor.execute(
                    "UPDATE model_registry SET is_active = FALSE WHERE model_name = %s AND symbol = %s AND is_active = TRUE",
                    (model_name, symbol),
                )
            cursor.execute(
                "INSERT INTO model_registry (model_name, mlflow_run_id, mlflow_version, symbol, stage, trained_at, promoted_at, in_sample_sharpe, oos_sharpe, feature_names, is_active) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    model_name,
                    mlflow_run_id,
                    mlflow_version,
                    symbol,
                    stage,
                    trained_at,
                    promoted_at,
                    in_sample_sharpe,
                    oos_sharpe,
                    json.dumps(feature_names),
                    is_active,
                ),
            )
            conn.commit()
            cursor.close()
            self.return_connection(conn)
            return True
        except Exception as e:
            logger.error(f"Error saving model registry entry: {str(e)}")
            try:
                conn.rollback()
                cursor.close()
                self.return_connection(conn)
            except Exception:
                pass
            return False

    def get_recent_drift_report(self, model_name: str, symbol: str, since: datetime) -> Optional[Dict[str, Any]]:
        try:
            conn = self.get_connection()
            query = (
                "SELECT * FROM drift_reports "
                "WHERE model_name = %s AND symbol = %s AND detected_at >= %s "
                "ORDER BY detected_at DESC LIMIT 1"
            )
            df = pd.read_sql(query, conn, params=[model_name, symbol, since])
            self.return_connection(conn)
            if df.empty:
                return None
            return df.iloc[0].to_dict()
        except Exception as e:
            logger.error(f"Error querying recent drift report: {str(e)}")
            self.return_connection(conn)
            return None

    def insert_drift_report(self, report: Dict[str, Any]) -> bool:
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO drift_reports (model_name, symbol, detected_at, features_checked, features_drifted, max_psi, mean_psi, drift_detected, severity, recommended_action, raw_psi_scores) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    report.get('model_name'),
                    report.get('symbol'),
                    report.get('detected_at'),
                    report.get('features_checked'),
                    json.dumps(report.get('features_drifted', [])),
                    report.get('max_psi'),
                    report.get('mean_psi'),
                    report.get('drift_detected'),
                    report.get('severity'),
                    report.get('recommended_action'),
                    json.dumps(report.get('raw_psi_scores', {})),
                ),
            )
            conn.commit()
            cursor.close()
            self.return_connection(conn)
            return True
        except Exception as e:
            logger.error(f"Error inserting drift report: {str(e)}")
            try:
                conn.rollback()
                cursor.close()
                self.return_connection(conn)
            except Exception:
                pass
            return False

    def block_model(self, model_name: str, symbol: str, reason: str) -> bool:
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO model_blocks (model_name, symbol, reason, blocked_at) VALUES (%s, %s, NOW())",
                (model_name, symbol, reason),
            )
            conn.commit()
            cursor.close()
            self.return_connection(conn)
            return True
        except Exception as e:
            logger.error(f"Error blocking model: {str(e)}")
            try:
                conn.rollback()
                cursor.close()
                self.return_connection(conn)
            except Exception:
                pass
            return False

    def unblock_model(self, model_name: str, symbol: str) -> bool:
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM model_blocks WHERE model_name = %s AND symbol = %s",
                (model_name, symbol),
            )
            conn.commit()
            cursor.close()
            self.return_connection(conn)
            return True
        except Exception as e:
            logger.error(f"Error unblocking model: {str(e)}")
            try:
                conn.rollback()
                cursor.close()
                self.return_connection(conn)
            except Exception:
                pass
            return False

    def get_block_status(self, model_name: str, symbol: str) -> Optional[Dict[str, Any]]:
        try:
            conn = self.get_connection()
            query = (
                "SELECT * FROM model_blocks WHERE model_name = %s AND symbol = %s "
                "ORDER BY blocked_at DESC LIMIT 1"
            )
            df = pd.read_sql(query, conn, params=[model_name, symbol])
            self.return_connection(conn)
            if df.empty:
                return None
            return df.iloc[0].to_dict()
        except Exception as e:
            logger.error(f"Error querying block status: {str(e)}")
            self.return_connection(conn)
            return None

    def close_pool(self):
        """Close all connections in the pool."""
        try:
            self.pool.closeall()
            logger.info("Connection pool closed")
        except Exception as e:
            logger.error(f"Error closing connection pool: {str(e)}")
