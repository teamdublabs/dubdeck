# Stage 1 — build the frontend
FROM node:22-alpine AS frontend
WORKDIR /fe
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2 — backend + built static assets
FROM python:3.12-slim
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev
COPY backend/app ./app
COPY --from=frontend /fe/dist ./static
# config.yaml is mounted at runtime (see compose.yaml / config.example.yaml),
# never baked into the image — it's deployment-specific data.

# Drop root. PUID is the runtime uid; set it to match the host user that owns
# the bind-mounted SSH key (mode 600) so it stays readable — e.g. on
# Docker-Desktop-for-Mac (VirtioFS ownership mapping) build with
# --build-arg PUID=501. Default 1000 suits a typical Linux host.
ARG PUID=1000
RUN useradd --uid ${PUID} --create-home dubdeck && mkdir /data && chown dubdeck /data
USER dubdeck

ENV DUBDECK_CONFIG=/config.yaml \
    DUBDECK_DB=/data/dubdeck.db \
    DUBDECK_STATIC=/app/static \
    DUBDECK_SSH_KEY=/run/secrets/ssh_key \
    DUBDECK_KNOWN_HOSTS=/run/secrets/known_hosts
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"]
# Run uvicorn from the synced venv directly — `uv run` would try to re-sync,
# which a read-only filesystem (and non-root user) forbids.
CMD ["/app/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
