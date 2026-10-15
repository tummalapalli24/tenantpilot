# syntax=docker/dockerfile:1.7
# TenantPilot backend image. One artifact, several process types - the command decides:
#   python -m apps.api                   control-plane API (default)
#   python -m services.worker            background ingestion worker (RQ)
#   python -m services.mcp_server        customer-operations MCP server
#   python -m services.mock_customer_api demo stand-in for a customer's system
#   python -m apps.api.cli migrate       DB bootstrap + migrations (initContainer / job)

FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
COPY pyproject.toml uv.lock ./
# Dependencies only (the layer is cached until the lockfile changes).
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project

FROM python:3.12-slim AS runtime
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OBJECT_STORE_DIR=/data/uploads
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data/uploads && chown app:app /data/uploads
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
# Code stays root-owned and read-only for the runtime user; only /data/uploads is writable.
COPY apps/__init__.py apps/__init__.py
COPY apps/api apps/api
COPY services services
COPY packages packages
COPY migrations migrations
COPY seed seed
COPY evals evals
COPY alembic.ini ./
USER 10001
EXPOSE 8000 8765 8081
CMD ["python", "-m", "apps.api"]
