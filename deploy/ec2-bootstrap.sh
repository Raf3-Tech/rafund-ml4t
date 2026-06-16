#!/usr/bin/env bash
# One-shot bootstrap for a fresh Ubuntu 22.04/24.04 EC2 instance.
# Run as the default user (ubuntu / ec2-user), not root.
# Usage:  bash deploy/ec2-bootstrap.sh
set -euo pipefail

log() { echo "[bootstrap] $*"; }

# ── 1. Docker ─────────────────────────────────────────────────────────────
log "Installing Docker Engine + Compose plugin..."
sudo apt-get update -y
sudo apt-get install -y ca-certificates curl gnupg lsb-release

sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
    https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update -y
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
log "Docker installed ($(docker --version))."

# ── 2. Clone from GitHub ──────────────────────────────────────────────────
GITHUB_REPO="https://github.com/Raf3-Tech/rafund-ml4t.git"
APP_DIR="/opt/rafund-ml4t"

log "Cloning repo to ${APP_DIR}..."
sudo git clone "${GITHUB_REPO}" "${APP_DIR}"
sudo chown -R "$USER:$USER" "${APP_DIR}"

# ── 3. Create .env from example ───────────────────────────────────────────
log "Creating .env from example..."
cp "${APP_DIR}/.env.example" "${APP_DIR}/.env"
log "Edit ${APP_DIR}/.env now: set DATABASE_URL, BINANCE_API_KEY, etc."

# ── 4. Start services ─────────────────────────────────────────────────────
log "Building and starting services..."
cd "${APP_DIR}"
newgrp docker << 'NEWGRP'
docker compose up -d --build
NEWGRP

# ── 5. Summary ────────────────────────────────────────────────────────────
log ""
log "Bootstrap complete. Useful commands:"
log ""
log "  docker compose ps                                  # service health"
log "  docker compose logs -f trainer                     # training log"
log "  docker compose logs -f dashboard                   # ops dashboard"
log "  docker compose exec trainer python main.py leaderboard"
log ""
log "  To update to latest code:"
log "    bash ${APP_DIR}/deploy/update.sh"
log ""
log "  ACCESS SERVICES (via SSH tunnel from your local machine):"
log "    ssh -L 8000:localhost:8000 -L 5000:localhost:5000 ubuntu@<EC2_PUBLIC_IP>"
log "    Then open:"
log "      http://localhost:8000  — ops dashboard"
log "      http://localhost:5000  — MLflow UI"
log ""
log "  RECOMMENDED EC2 INSTANCE TYPES:"
log "    t3.medium  (2 vCPU /  4 GB) — light workloads, cheapest"
log "    c5.xlarge  (4 vCPU /  8 GB) — comfortable for full engine runs"
log "    c5.2xlarge (8 vCPU / 16 GB) — fastest training cycles"
log "    Storage: 30 GB gp3 EBS"
