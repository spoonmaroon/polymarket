FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock README.md /app/
COPY src /app/src
COPY deploy/collector/collector-entrypoint.sh /app/collector-entrypoint.sh

RUN uv sync --frozen --no-dev
RUN chmod 755 /app/collector-entrypoint.sh

ENV PATH="/app/.venv/bin:${PATH}"

ENTRYPOINT ["/app/collector-entrypoint.sh"]
