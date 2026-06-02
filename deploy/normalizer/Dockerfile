FROM python:3.14-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
COPY deploy/normalizer/normalizer-entrypoint.sh /usr/local/bin/normalizer-entrypoint.sh

RUN pip install --no-cache-dir . \
    && chmod 755 /usr/local/bin/normalizer-entrypoint.sh

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/normalizer-entrypoint.sh"]
