# syntax=docker/dockerfile:1

# ── Asset build stage: bundles the dashboard CSS/JS with esbuild. ───────────
# Ephemeral — Node never ships in the runtime image below.
FROM node:20-slim AS assets
WORKDIR /assets
COPY monitoring/static/src ./src
COPY monitoring/static/build.sh ./build.sh
RUN npm install --no-save esbuild \
    && chmod +x build.sh && ./build.sh

FROM python:3.12-slim

WORKDIR /app

# libpq-dev + gcc required to build psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Deps layer — cached until requirements.txt changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=assets /assets/dist ./monitoring/static/dist

RUN useradd -m -u 1001 appuser \
    && mkdir -p logs tmp results \
    && chown -R appuser:appuser /app \
    && chmod +x /app/scripts/entrypoint.sh /app/scripts/train_loop.sh

USER appuser

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
CMD ["python", "main.py", "collect"]
