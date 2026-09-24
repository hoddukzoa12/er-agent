# Sandbox image for running the ER dispatch agent inside NVIDIA OpenShell.
# Build context: repository root.
#   docker build -f deploy/openshell/Dockerfile -t er-agent-sandbox:latest .
# OpenShell replaces CMD with its supervisor; the start command is passed after `--`.
FROM python:3.13-slim

# iproute2: sandbox network namespace; nftables: bypass detection
RUN apt-get update && apt-get install -y --no-install-recommends curl iproute2 nftables ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv

RUN groupadd --gid 1500 app && useradd --uid 1500 --gid app --create-home app

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY configs ./configs
ENV UV_PROJECT_ENVIRONMENT=/app/.venv UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
RUN uv sync --frozen --no-dev

# Code stays read-only in /app; the sandbox workspace (image WORKDIR) must be writable.
RUN install -d -o app -g app /sandbox
WORKDIR /sandbox
ENV PATH=/app/.venv/bin:$PATH \
    ER_CACHE_DIR=/sandbox/cache \
    ER_VOICE=0 \
    TZ=Asia/Seoul \
    PYTHONUNBUFFERED=1
USER app
CMD ["er-agent", "serve", "--host", "127.0.0.1", "--port", "8000"]
