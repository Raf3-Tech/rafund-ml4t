#!/usr/bin/env bash
# Container entrypoint: waits for Postgres, runs Alembic migrations, then execs CMD.
set -euo pipefail

log() { echo "[entrypoint] $*"; }

log "Waiting for PostgreSQL..."
until python - <<'PYEOF' 2>/dev/null
import os, sys
sys.path.insert(0, "/app")
from config.loader import build_database_url
url = build_database_url(for_sqlalchemy=False)
if not url:
    print("No DATABASE_URL or DB_* vars — cannot connect", file=sys.stderr)
    sys.exit(1)
import psycopg2
psycopg2.connect(url).close()
PYEOF
do
    log "DB not ready — retrying in 3s..."
    sleep 3
done
log "PostgreSQL is ready."

log "Running Alembic migrations..."
alembic upgrade head
log "Migrations complete."

exec "$@"
