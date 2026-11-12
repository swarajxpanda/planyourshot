# ---- frontend build ----
FROM node:22-slim AS web
WORKDIR /web
# Frontend error tracking is baked in at build time: docker build --build-arg VITE_SENTRY_DSN=...
ARG VITE_SENTRY_DSN=""
ENV VITE_SENTRY_DSN=$VITE_SENTRY_DSN
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --legacy-peer-deps
COPY frontend/ ./
RUN npm run build

# ---- app ----
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev
COPY alembic.ini ./
COPY data/knowledge ./data/knowledge
COPY legal ./legal
COPY --from=web /web/dist ./frontend/dist
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
 && useradd --system --uid 10001 --home /app app \
 && mkdir -p /data/uploads && chown -R app /data

# Defaults for a single container with SQLite. docker-compose.yml switches to Postgres.
ENV DATABASE_URL=sqlite:////data/app.db STORAGE_DIR=/data/uploads
VOLUME /data
USER app
EXPOSE 8000
# Same image for every role: `web` (default) or `worker`. Migrations run on start
# (AUTO_MIGRATE=1); on Postgres they are guarded by a lock, so both roles can start together.
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["web"]
