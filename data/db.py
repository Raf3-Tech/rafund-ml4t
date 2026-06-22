"""
PostgreSQL database connection and operations.

This module handles all database interactions including
connection pooling, data insertion, and querying.
"""

import json
import os
import re
import psycopg2
from psycopg2 import sql
from psycopg2.pool import ThreadedConnectionPool
from psycopg2.extras import execute_values
import pandas as pd
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from config.loader import build_database_url
from config.logging_config import get_logger

logger = get_logger(__name__)

# Load environment variables from .env. python-dotenv correctly handles quoted
# values, inline comments and `export` prefixes that the previous hand-rolled
# parser mishandled. override=False so real environment variables take
# precedence over the file (the conventional precedence).
def load_env() -> None:
    load_dotenv(Path(__file__).parent.parent / ".env", override=False)


load_env()

# Tables that may be referenced by identifier in dynamically-built SQL
# (COUNT/DELETE-all helpers). Any table interpolated into a query must be a
# member of this allowlist; combined with psycopg2.sql.Identifier quoting this
# closes off identifier-injection from a programming error upstream.
CORE_TABLES = (
    "prices",
    "features",
    "signals",
    "trades",
    "portfolio",
    "backtest_results",
    # Model-lifecycle tables (created by alembic migration 0002).
    "model_registry",
    "drift_reports",
    "model_blocks",
    # Research decisions (created by alembic migration 0004).
    "research_decisions",
)


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
        max_conn: int = 10
    ):
        self.database_url = database_url or os.getenv('DATABASE_URL')
        self.host = host or os.getenv('DB_HOST', 'localhost')
        self.port = int(port or os.getenv('DB_PORT', 5432))
        self.database = database or os.getenv('DB_NAME', 'rafund')
        self.user = user or os.getenv('DB_USER', 'postgres')
        # No hardcoded password fallback: rely on DB_PASSWORD (loaded from .env).
        # A missing password yields None so libpq surfaces it clearly instead of
        # silently authenticating with a guessable default.
        self.password = password or os.getenv('DB_PASSWORD')
        self.min_conn = min_conn
        self.max_conn = max_conn
        self.pool = None
        self._engine = None  # lazily-built SQLAlchemy engine for pandas reads
        self._initialize_pool()

    def _initialize_pool(self):
        """Initialize connection pool."""
        try:
            # Single source of truth for the connection target (DATABASE_URL or
            # DB_* components, incl. optional DB_SSLMODE for hosted Postgres).
            dsn = build_database_url(
                for_sqlalchemy=False,
                database_url=self.database_url,
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.user,
                password=self.password,
            )

            # ThreadedConnectionPool is required: the retraining scheduler runs
            # concurrent jobs (drift checks, retraining, health) that check out
            # connections from multiple threads. SimpleConnectionPool is not
            # thread-safe and corrupts its free-list under concurrency.
            self.pool = ThreadedConnectionPool(
                self.min_conn,
                self.max_conn,
                dsn=dsn,
            )
            logger.info(f"Database connection pool initialized for {self.database}")
        except Exception as e:
            logger.error(f"Failed to initialize connection pool: {str(e)}")
            raise

    @property
    def engine(self):
        """Lazily-built SQLAlchemy engine used for pandas read queries.

        pandas 2.x only officially supports a SQLAlchemy connectable (a raw
        psycopg2 connection emits a deprecation warning and is "not tested"), so
        all ``read_sql`` traffic goes through this engine. It is independent of
        the psycopg2 write pool above.
        """
        if self._engine is None:
            url = build_database_url(
                for_sqlalchemy=True,
                database_url=self.database_url,
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.user,
                password=self.password,
            )
            self._engine = create_engine(url, pool_pre_ping=True)
        return self._engine

    def read_sql(self, query: str, params: Optional[list] = None) -> pd.DataFrame:
        """Run a read-only query through the SQLAlchemy engine.

        Accepts the legacy positional ``%s`` placeholder style with a list of
        params and converts it to SQLAlchemy named bind parameters, so existing
        queries work unchanged while staying clean under pandas 2.x.
        """
        if params:
            bound = {f"p{i}": v for i, v in enumerate(params)}
            counter = {"i": 0}

            def _sub(_match: "re.Match") -> str:
                key = f":p{counter['i']}"
                counter["i"] += 1
                return key

            sql_text = text(re.sub(r"%s", _sub, query))
            with self.engine.connect() as conn:
                return pd.read_sql(sql_text, conn, params=bound)
        with self.engine.connect() as conn:
            return pd.read_sql(text(query), conn)

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
        if conn is None:
            return
        try:
            self.pool.putconn(conn)
        except Exception as e:
            logger.error(f"Error returning connection: {str(e)}")

    def test_connection(self) -> bool:
        """Test database connection."""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            logger.info("Database connection test passed")
            return True
        except Exception as e:
            logger.error(f"Database connection test failed: {str(e)}")
            return False
        finally:
            if cursor:
                cursor.close()
            self.return_connection(conn)

    def insert_prices(self, df: pd.DataFrame, exchange: str = "binance") -> int:
        """
        Insert OHLCV data into prices table.

        Args:
            df: DataFrame with columns: timestamp, open, high, low, close, volume, symbol
            exchange: Exchange the data was collected from (e.g. "binance", "kraken")

        Returns:
            Number of rows inserted
        """
        if df.empty:
            logger.warning("Empty DataFrame provided to insert_prices")
            return 0

        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            data = []
            for _, row in df.iterrows():
                data.append((
                    row['symbol'],
                    row['timestamp'],
                    float(row['open']),
                    float(row['high']),
                    float(row['low']),
                    float(row['close']),
                    float(row['volume']),
                    exchange,
                ))

            query = """
                INSERT INTO prices (symbol, timestamp, open, high, low, close, volume, exchange)
                VALUES %s
                ON CONFLICT (exchange, symbol, timestamp) DO NOTHING
            """

            execute_values(cursor, query, data)
            conn.commit()

            inserted = cursor.rowcount
            logger.info(f"Inserted {inserted} price records into database")
            return inserted

        except Exception as e:
            logger.error(f"Error inserting prices: {str(e)}")
            if conn:
                conn.rollback()
            return 0
        finally:
            if cursor:
                cursor.close()
            self.return_connection(conn)

    def get_prices(
        self,
        symbol: str,
        start_date: datetime = None,
        end_date: datetime = None,
        exchange: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Retrieve price data from database.

        Args:
            symbol: Trading symbol
            start_date: Start date (optional)
            end_date: End date (optional)
            exchange: Restrict to one exchange (optional; default = all exchanges)

        Returns:
            DataFrame with price data
        """
        try:
            query = "SELECT * FROM prices WHERE symbol = %s"
            params = [symbol]

            if exchange:
                query += " AND exchange = %s"
                params.append(exchange)

            if start_date:
                query += " AND timestamp >= %s"
                params.append(start_date)

            if end_date:
                query += " AND timestamp <= %s"
                params.append(end_date)

            query += " ORDER BY timestamp"

            df = self.read_sql(query, params)

            logger.info(f"Retrieved {len(df)} price records for {symbol}")
            return df

        except Exception as e:
            logger.error(f"Error retrieving prices: {str(e)}")
            return pd.DataFrame()

    def get_latest_timestamp(self, symbol: str, exchange: str = "binance") -> Optional[datetime]:
        """
        Get the latest timestamp for a symbol on a given exchange.

        Args:
            symbol: Trading symbol
            exchange: Exchange to filter by (default "binance")

        Returns:
            Latest timestamp or None if no data
        """
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT MAX(timestamp) FROM prices WHERE symbol = %s AND exchange = %s",
                (symbol, exchange)
            )
            result = cursor.fetchone()
            return result[0] if result[0] else None
        except Exception as e:
            logger.error(f"Error getting latest timestamp: {str(e)}")
            return None
        finally:
            if cursor:
                cursor.close()
            self.return_connection(conn)

    def get_symbols_with_data(self, exchange: Optional[str] = None) -> List[str]:
        """
        Get all symbols that have data in the database.

        Args:
            exchange: Restrict to one exchange (optional; default = all exchanges)

        Returns:
            List of symbols
        """
        try:
            if exchange:
                query = "SELECT DISTINCT symbol FROM prices WHERE exchange = %s ORDER BY symbol"
                df = self.read_sql(query, [exchange])
            else:
                query = "SELECT DISTINCT symbol FROM prices ORDER BY symbol"
                df = self.read_sql(query)

            symbols = df['symbol'].tolist()
            logger.info(f"Found {len(symbols)} symbols in database")
            return symbols

        except Exception as e:
            logger.error(f"Error getting symbols: {str(e)}")
            return []

    def get_feature_pairs(self) -> List[tuple]:
        """
        Get all unique stationary feature pairs from the features table.

        Returns:
            List of (symbol_a, symbol_b) tuples
        """
        try:
            query = "SELECT DISTINCT symbol_a, symbol_b FROM features ORDER BY symbol_a, symbol_b"
            df = self.read_sql(query)
            pairs = [(row['symbol_a'], row['symbol_b']) for _, row in df.iterrows()]
            logger.info(f"Found {len(pairs)} feature pairs in database")
            return pairs
        except Exception as e:
            logger.error(f"Error getting feature pairs: {str(e)}")
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

        conn = None
        cursor = None
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

            execute_values(cursor, query, data, page_size=max(len(data), 1))
            conn.commit()

            # rowcount reflects rows actually written (ON CONFLICT DO NOTHING
            # skips duplicates); page_size above keeps it a single statement so
            # the count is accurate rather than just the last page.
            inserted = cursor.rowcount
            logger.info(f"Inserted {inserted} feature records into database")
            return inserted

        except Exception as e:
            logger.error(f"Error inserting features: {str(e)}")
            if conn:
                conn.rollback()
            return 0
        finally:
            if cursor:
                cursor.close()
            self.return_connection(conn)

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

        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            def _to_signal_int(v) -> int:
                # int() truncates toward zero: int(-0.85) = 0, losing the B-leg
                # direction entirely when hedge_ratio < 1. round() then int() gives
                # the nearest integer, preserving -1/+1 for fractional hedge ratios.
                try:
                    return int(round(float(v)))
                except (TypeError, ValueError):
                    return 0

            data = []
            for _, row in df.iterrows():
                data.append((
                    row['symbol_a'],
                    row['symbol_b'],
                    row['timestamp'],
                    row['signal'],
                    float(row.get('z_score')),
                    _to_signal_int(row.get('position_a', 0)),
                    _to_signal_int(row.get('position_b', 0))
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
            return inserted

        except Exception as e:
            logger.error(f"Error inserting signals: {str(e)}")
            if conn:
                conn.rollback()
            return 0
        finally:
            if cursor:
                cursor.close()
            self.return_connection(conn)

    def get_data_stats(self) -> Dict:
        """
        Get statistics about data in the database.

        Returns:
            Dictionary with counts and date ranges
        """
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            stats = {}

            cursor.execute("SELECT COUNT(*) FROM prices")
            stats['total_price_records'] = cursor.fetchone()[0]

            cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM prices")
            min_date, max_date = cursor.fetchone()
            stats['min_date'] = min_date
            stats['max_date'] = max_date

            cursor.execute("SELECT COUNT(DISTINCT symbol) FROM prices")
            stats['num_symbols'] = cursor.fetchone()[0]

            logger.info(f"Database stats: {stats['total_price_records']} records, "
                       f"{stats['num_symbols']} symbols")
            return stats

        except Exception as e:
            logger.error(f"Error getting data stats: {str(e)}")
            return {}
        finally:
            if cursor:
                cursor.close()
            self.return_connection(conn)

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

        conn = None
        cursor = None
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
            logger.info(f"Saved backtest results for {results.get('backtest_id')}")
            return True

        except Exception as e:
            logger.error(f"Error inserting backtest results: {str(e)}")
            if conn:
                conn.rollback()
            return False
        finally:
            if cursor:
                cursor.close()
            self.return_connection(conn)

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

        conn = None
        cursor = None
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
            return inserted

        except Exception as e:
            logger.error(f"Error inserting trades: {str(e)}")
            if conn:
                conn.rollback()
            return 0
        finally:
            if cursor:
                cursor.close()
            self.return_connection(conn)

    def insert_portfolio(self, df: pd.DataFrame) -> int:
        """
        Insert portfolio snapshots into portfolio table.

        Expected columns: timestamp, symbol, position_size, entry_price,
        current_price, unrealized_pnl
        """
        if df.empty:
            return 0
        conn = None
        cursor = None
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
            logger.info(f"Upserted {inserted} portfolio snapshots")
            return inserted
        except Exception as e:
            logger.error(f"Error inserting portfolio: {str(e)}")
            if conn:
                conn.rollback()
            return 0
        finally:
            if cursor:
                cursor.close()
            self.return_connection(conn)

    def delete_signals_for_pair(self, symbol_a: str, symbol_b: str) -> int:
        """Remove signals for a pair before re-inserting."""
        conn = None
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
            logger.info(f"Deleted {deleted} signals for {symbol_a}/{symbol_b}")
            return deleted
        except Exception as e:
            logger.error(f"Error deleting signals: {str(e)}")
            if conn is not None:
                conn.rollback()
            return 0
        finally:
            if conn is not None:
                self.return_connection(conn)

    def delete_features_for_pair(self, symbol_a: str, symbol_b: str) -> int:
        """Remove features for a pair before re-calculating."""
        conn = None
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
            logger.info(f"Deleted {deleted} features for {symbol_a}/{symbol_b}")
            return deleted
        except Exception as e:
            logger.error(f"Error deleting features: {str(e)}")
            if conn is not None:
                conn.rollback()
            return 0
        finally:
            if conn is not None:
                self.return_connection(conn)

    def delete_trades_for_symbol(self, symbol: str) -> int:
        """Remove trades for a spread symbol label before re-inserting."""
        conn = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM trades WHERE symbol = %s", (symbol,))
            deleted = cursor.rowcount
            conn.commit()
            cursor.close()
            return deleted
        except Exception as e:
            logger.error(f"Error deleting trades: {str(e)}")
            if conn is not None:
                conn.rollback()
            return 0
        finally:
            if conn is not None:
                self.return_connection(conn)

    def delete_portfolio_for_symbol(self, symbol: str) -> int:
        """Remove portfolio rows for a symbol label."""
        conn = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM portfolio WHERE symbol = %s", (symbol,))
            deleted = cursor.rowcount
            conn.commit()
            cursor.close()
            return deleted
        except Exception as e:
            logger.error(f"Error deleting portfolio: {str(e)}")
            if conn is not None:
                conn.rollback()
            return 0
        finally:
            if conn is not None:
                self.return_connection(conn)

    def get_table_counts(self) -> Dict[str, int]:
        """Row counts for all core tables."""
        counts = {}
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            for table in CORE_TABLES:
                cursor.execute(
                    sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table))
                )
                counts[table] = cursor.fetchone()[0]
            return counts
        except Exception as e:
            logger.error(f"Error getting table counts: {str(e)}")
            return counts
        finally:
            if cursor:
                cursor.close()
            self.return_connection(conn)

    def get_active_production_model(self, model_name: str, symbol: str) -> Optional[Dict[str, Any]]:
        try:
            query = (
                "SELECT * FROM model_registry "
                "WHERE model_name = %s AND symbol = %s AND stage = 'Production' AND is_active = TRUE "
                "ORDER BY promoted_at DESC LIMIT 1"
            )
            df = self.read_sql(query, [model_name, symbol])
            if df.empty:
                return None
            return df.iloc[0].to_dict()
        except Exception as e:
            logger.error(f"Error querying active production model: {str(e)}")
            return None

    def get_production_models(self) -> list[Dict[str, Any]]:
        try:
            query = "SELECT * FROM model_registry WHERE stage = 'Production' AND is_active = TRUE ORDER BY promoted_at DESC"
            df = self.read_sql(query)
            return df.to_dict(orient='records')
        except Exception as e:
            logger.error(f"Error querying production models: {str(e)}")
            return []

    def get_all_model_registry_entries(self) -> list[Dict[str, Any]]:
        try:
            query = "SELECT * FROM model_registry ORDER BY model_name, symbol, promoted_at DESC NULLS LAST"
            df = self.read_sql(query)
            return df.to_dict(orient='records')
        except Exception as e:
            logger.error(f"Error querying model registry entries: {str(e)}")
            return []

    def get_latest_drift_reports(self) -> list[Dict[str, Any]]:
        try:
            query = (
                "SELECT DISTINCT ON (model_name, symbol) model_name, symbol, severity, detected_at "
                "FROM drift_reports "
                "ORDER BY model_name, symbol, detected_at DESC"
            )
            df = self.read_sql(query)
            return df.to_dict(orient='records')
        except Exception as e:
            logger.error(f"Error querying latest drift reports: {str(e)}")
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
        conn = None
        cursor = None
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
            return True
        except Exception as e:
            logger.error(f"Error saving model registry entry: {str(e)}")
            if conn:
                conn.rollback()
            return False
        finally:
            if cursor:
                cursor.close()
            self.return_connection(conn)

    def get_recent_drift_report(self, model_name: str, symbol: str, since: datetime) -> Optional[Dict[str, Any]]:
        try:
            query = (
                "SELECT * FROM drift_reports "
                "WHERE model_name = %s AND symbol = %s AND detected_at >= %s "
                "ORDER BY detected_at DESC LIMIT 1"
            )
            df = self.read_sql(query, [model_name, symbol, since])
            if df.empty:
                return None
            return df.iloc[0].to_dict()
        except Exception as e:
            logger.error(f"Error querying recent drift report: {str(e)}")
            return None

    def insert_drift_report(self, report: Dict[str, Any]) -> bool:
        conn = None
        cursor = None
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
            return True
        except Exception as e:
            logger.error(f"Error inserting drift report: {str(e)}")
            if conn:
                conn.rollback()
            return False
        finally:
            if cursor:
                cursor.close()
            self.return_connection(conn)

    def block_model(self, model_name: str, symbol: str, reason: str) -> bool:
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO model_blocks (model_name, symbol, reason, blocked_at) VALUES (%s, %s, %s, NOW())",
                (model_name, symbol, reason),
            )
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error blocking model: {str(e)}")
            if conn:
                conn.rollback()
            return False
        finally:
            if cursor:
                cursor.close()
            self.return_connection(conn)

    def unblock_model(self, model_name: str, symbol: str) -> bool:
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM model_blocks WHERE model_name = %s AND symbol = %s",
                (model_name, symbol),
            )
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error unblocking model: {str(e)}")
            if conn:
                conn.rollback()
            return False
        finally:
            if cursor:
                cursor.close()
            self.return_connection(conn)

    def get_block_status(self, model_name: str, symbol: str) -> Optional[Dict[str, Any]]:
        try:
            query = (
                "SELECT * FROM model_blocks WHERE model_name = %s AND symbol = %s "
                "ORDER BY blocked_at DESC LIMIT 1"
            )
            df = self.read_sql(query, [model_name, symbol])
            if df.empty:
                return None
            return df.iloc[0].to_dict()
        except Exception as e:
            logger.error(f"Error querying block status: {str(e)}")
            return None

    def insert_engine_results(self, rows: List[Dict[str, Any]]) -> int:
        """Insert walk-forward engine results into engine_results table.

        Every result — pass or fail — is written.  Caller must ensure the
        engine_results table exists (run alembic upgrade head first).
        """
        if not rows:
            return 0
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            data = [
                (
                    r["run_id"],
                    r["strategy_name"],
                    r["symbol"],
                    r["window_type"],
                    r["window_start"],
                    r["window_end"],
                    float(r["window_years"]),
                    json.dumps(r["params"]) if not isinstance(r["params"], str) else r["params"],
                    r.get("total_return_pct"),
                    r.get("sharpe_ratio"),
                    r.get("max_drawdown_pct"),
                    r.get("win_rate_pct"),
                    r.get("num_trades"),
                    r.get("conservative_pass"),
                    r.get("standard_pass"),
                    r.get("permissive_pass"),
                    r.get("failure_reason"),
                    int(r.get("mutation_generation", 0)),
                    r.get("parent_run_id"),
                    r.get("bars_used"),
                    r.get("regime_trend"),
                    r.get("regime_volatility"),
                    r.get("regime_direction"),
                )
                for r in rows
            ]
            query = """
                INSERT INTO engine_results (
                    run_id, strategy_name, symbol, window_type,
                    window_start, window_end, window_years, params,
                    total_return_pct, sharpe_ratio, max_drawdown_pct, win_rate_pct, num_trades,
                    conservative_pass, standard_pass, permissive_pass,
                    failure_reason, mutation_generation, parent_run_id, bars_used,
                    regime_trend, regime_volatility, regime_direction
                ) VALUES %s
            """
            execute_values(cursor, query, data)
            conn.commit()
            inserted = cursor.rowcount
            logger.info("Inserted %d engine_results rows", inserted)
            return inserted
        except Exception as e:
            logger.error("Error inserting engine_results: %s", e)
            if conn:
                conn.rollback()
            return 0
        finally:
            if cursor:
                cursor.close()
            self.return_connection(conn)

    def insert_funding_rates(self, df: pd.DataFrame) -> int:
        """Insert funding rate rows into funding_rates table.

        Deduplicates on (symbol, funding_time) via ON CONFLICT DO NOTHING.
        """
        if df.empty:
            return 0
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            data = [
                (
                    row["symbol"],
                    row["funding_time"],
                    float(row["funding_rate"]),
                    float(row["mark_price"]) if pd.notna(row.get("mark_price")) else None,
                )
                for _, row in df.iterrows()
            ]
            query = """
                INSERT INTO funding_rates (symbol, funding_time, funding_rate, mark_price)
                VALUES %s
                ON CONFLICT (symbol, funding_time) DO NOTHING
            """
            execute_values(cursor, query, data)
            conn.commit()
            inserted = cursor.rowcount
            logger.info("Inserted %d funding_rate rows", inserted)
            return inserted
        except Exception as e:
            logger.error("Error inserting funding_rates: %s", e)
            if conn:
                conn.rollback()
            return 0
        finally:
            if cursor:
                cursor.close()
            self.return_connection(conn)

    def get_latest_funding_time_ms(self, symbol: str) -> Optional[int]:
        """Return the latest stored funding_time as Unix milliseconds, or None."""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT MAX(funding_time) FROM funding_rates WHERE symbol = %s",
                (symbol,),
            )
            result = cursor.fetchone()
            if result and result[0] is not None:
                ts = result[0]
                if hasattr(ts, "timestamp"):
                    return int(ts.timestamp() * 1000)
            return None
        except Exception as e:
            logger.error("Error getting latest funding time for %s: %s", symbol, e)
            return None
        finally:
            if cursor:
                cursor.close()
            self.return_connection(conn)

    def get_funding_rates(self, symbol: str) -> pd.DataFrame:
        """Return funding-rate history for one symbol, ascending by time.

        Columns: ``timestamp`` (funding_time), ``close`` (the funding_rate, aliased
        so the funding strategy can consume it like an OHLCV frame),
        ``funding_rate`` and ``mark_price``. Empty frame if none / on error.
        """
        try:
            df = self.read_sql(
                """
                SELECT funding_time AS timestamp,
                       funding_rate AS close,
                       funding_rate,
                       mark_price
                FROM funding_rates
                WHERE symbol = %s
                ORDER BY funding_time ASC
                """,
                [symbol],
            )
            if not df.empty:
                df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            return df
        except Exception as e:
            logger.error("Error getting funding rates for %s: %s", symbol, e)
            return pd.DataFrame()

    def insert_research_decision(self, decision: Dict[str, Any]) -> bool:
        """Insert one research decision row into research_decisions."""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO research_decisions (
                    created_at, strategy_name, symbol, accepted, reason,
                    avg_sharpe, avg_max_dd_pct, avg_win_rate_pct,
                    pass_ratio, n_windows, total_num_trades, params
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    decision.get("timestamp"),
                    decision["strategy_name"],
                    decision.get("symbol"),
                    bool(decision["accepted"]),
                    decision["reason"],
                    float(decision["avg_sharpe"]),
                    float(decision["avg_max_dd_pct"]),
                    float(decision["avg_win_rate_pct"]),
                    float(decision["pass_ratio"]),
                    int(decision["n_windows"]),
                    int(decision.get("total_num_trades", 0)),
                    json.dumps(decision.get("params") or {}),
                ),
            )
            conn.commit()
            return True
        except Exception as e:
            logger.error("Error inserting research_decision: %s", e)
            if conn:
                conn.rollback()
            return False
        finally:
            if cursor:
                cursor.close()
            self.return_connection(conn)

    def get_research_decisions(self, last: int = 50) -> List[Dict[str, Any]]:
        """Return the most recent research decisions, newest first."""
        try:
            df = self.read_sql(
                """
                SELECT created_at, strategy_name, symbol, accepted, reason,
                       avg_sharpe, avg_max_dd_pct, avg_win_rate_pct,
                       pass_ratio, n_windows, params
                FROM research_decisions
                ORDER BY created_at DESC
                LIMIT %s
                """,
                [last],
            )
            rows = []
            for _, r in df.iterrows():
                rows.append({
                    "timestamp": r["created_at"].isoformat() if hasattr(r["created_at"], "isoformat") else str(r["created_at"]),
                    "strategy_name": str(r["strategy_name"]),
                    "symbol": r["symbol"] if r["symbol"] is not None else None,
                    "accepted": bool(r["accepted"]),
                    "reason": str(r["reason"]),
                    "avg_sharpe": float(r["avg_sharpe"]),
                    "avg_max_dd_pct": float(r["avg_max_dd_pct"]),
                    "avg_win_rate_pct": float(r["avg_win_rate_pct"]),
                    "pass_ratio": float(r["pass_ratio"]),
                    "n_windows": int(r["n_windows"]),
                    "params": r["params"] if isinstance(r["params"], dict) else {},
                })
            return rows
        except Exception as e:
            logger.error("Error fetching research_decisions: %s", e)
            return []

    def close_pool(self):
        """Close all connections in the pool (and dispose the read engine)."""
        try:
            self.pool.closeall()
            if self._engine is not None:
                self._engine.dispose()
            logger.info("Connection pool closed")
        except Exception as e:
            logger.error(f"Error closing connection pool: {str(e)}")
