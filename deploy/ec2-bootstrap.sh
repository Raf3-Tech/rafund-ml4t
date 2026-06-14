#!/usr/bin/env bash
# One-shot bootstrap for a fresh Ubuntu 22.04/24.04 EC2 instance.
# Run as the default user (ubuntu / ec2-user), not root.
# Usage:  bash deploy/ec2-bootstrap.sh
set -euo pipefail

APP_DIR="/opt/rafund-ml4t"

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

# ── 2. App directory ──────────────────────────────────────────────────────
log "Creating app directory at ${APP_DIR}..."
sudo mkdir -p "${APP_DIR}"
sudo chown "$USER:$USER" "${APP_DIR}"

# ── 3. Instructions ───────────────────────────────────────────────────────
log ""
log "Bootstrap complete. Follow these steps to start training:"
log ""
log "  STEP 1 — Copy the project from your local machine:"
log "    rsync -avz --exclude venv --exclude .git --exclude mlruns \\"
log "        /home/raf3/Rafund/rafund-ml4t/ ubuntu@<EC2_PUBLIC_IP>:${APP_DIR}/"
log ""
log "  STEP 2 — SSH into the instance and create .env:"
log "    ssh ubuntu@<EC2_PUBLIC_IP>"
log "    cp ${APP_DIR}/.env.aws.example ${APP_DIR}/.env"
log "    nano ${APP_DIR}/.env   # set DB_PASSWORD, BINANCE_API_KEY, etc."
log ""
log "  STEP 3 — Build images and start all services:"
log "    cd ${APP_DIR}"
log "    newgrp docker   # activate docker group without re-login"
log "    docker compose up -d --build"
log ""
log "  STEP 4 — Watch training output:"
log "    docker compose logs -f trainer"
log ""
log "  USEFUL COMMANDS:"
log "    docker compose ps                     # service health"
log "    docker compose logs -f trainer        # training log"
log "    docker compose logs -f dashboard      # ops dashboard log"
log "    docker compose exec trainer python main.py leaderboard"
log ""
log "  ACCESS SERVICES (via SSH tunnel from your local machine):"
log "    ssh -L 8000:localhost:8000 -L 5000:localhost:5000 ubuntu@<EC2_PUBLIC_IP>"
log "    Then open:"
log "      http://localhost:8000  — ops dashboard"
log "      http://localhost:5000  — MLflow UI"
log ""
log "  RECOMMENDED EC2 INSTANCE TYPES:"
log "    t3.medium  (2 vCPU / 4 GB)  — light workloads, cheapest"
log "    c5.xlarge  (4 vCPU / 8 GB)  — comfortable for full engine runs"
log "    c5.2xlarge (8 vCPU / 16 GB) — fastest training cycles"
log "    Storage: 30 GB gp3 EBS (increase to 50 GB for multi-year data)"
