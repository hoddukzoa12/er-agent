# Cloudflare Containers image (linux/amd64). Build context: repository root (see wrangler.jsonc).
FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY configs ./configs
ENV UV_PROJECT_ENVIRONMENT=/app/.venv UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
RUN uv sync --frozen --no-dev
# Container disk is ephemeral: snapshots, call audio and the rejection board live in /tmp.
ENV ER_CACHE_DIR=/tmp/er-cache \
    ER_VOICE=1 \
    TZ=Asia/Seoul \
    PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["/app/.venv/bin/er-agent", "serve", "--host", "0.0.0.0", "--port", "8000"]
