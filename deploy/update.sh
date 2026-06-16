#!/usr/bin/env bash
# Manual update: git pull + rebuild + restart all services.
# Run from /opt/rafund-ml4t
set -euo pipefail

APP_DIR="/opt/rafund-ml4t"
cd "${APP_DIR}"

echo "[update] Pulling latest from GitHub..."
git pull origin main

echo "[update] Rebuilding and restarting services..."
docker compose up -d --build --remove-orphans

echo "[update] Pruning old images..."
docker image prune -f

echo "[update] Done. Service status:"
docker compose ps
