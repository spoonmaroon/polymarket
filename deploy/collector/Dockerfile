FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock README.md /app/
COPY src /app/src

RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:${PATH}"

ENTRYPOINT ["polymarket-engine"]
CMD ["collect", "--assets", "BTC,ETH", "--intervals", "5m,15m", "--forever", "--windows-to-track", "2", "--raw-root", "/var/lib/polymarket/raw", "--duckdb-path", "/var/lib/polymarket/db/polymarket.duckdb", "--status-path", "/var/lib/polymarket/live/status.json", "--max-batch-size", "100", "--snapshot-interval", "1", "--clob-rest-backup-interval", "15", "--market-refresh-interval", "30", "--display-timezone", "America/Chicago"]
