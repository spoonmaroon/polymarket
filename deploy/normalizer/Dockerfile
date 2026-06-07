FROM python:3.14-slim-bookworm

WORKDIR /app

RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
COPY ui/dist ./ui/dist
COPY deploy/normalizer/normalizer-entrypoint.sh /usr/local/bin/normalizer-entrypoint.sh

RUN python -m pip install --no-cache-dir --upgrade "pip>=26.1.2" \
    && python -m pip install --no-cache-dir . \
    && python -m pip uninstall -y pip \
    && chmod 755 /usr/local/bin/normalizer-entrypoint.sh

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/normalizer-entrypoint.sh"]
