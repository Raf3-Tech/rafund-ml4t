"""Shared database connection factory for CLI commands."""

from __future__ import annotations

import os

from data.db import DatabaseConnection


def get_db_connection() -> DatabaseConnection:
    return DatabaseConnection(
        database_url=os.getenv("DATABASE_URL"),
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 5432)),
        database=os.getenv("DB_NAME", "rafund"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD"),
    )
