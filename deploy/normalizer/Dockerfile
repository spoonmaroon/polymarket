FROM node:22-bookworm-slim AS ui-builder

WORKDIR /app/ui

COPY ui/package.json ui/package-lock.json ./
RUN npm ci

COPY ui/index.html ui/tsconfig.json ui/vite.config.ts ./
COPY ui/src ./src
RUN npm run build

FROM python:3.14-slim-bookworm

WORKDIR /app

RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
COPY --from=ui-builder /app/ui/dist ./ui/dist
COPY deploy/normalizer/normalizer-entrypoint.sh /usr/local/bin/normalizer-entrypoint.sh

RUN python -m pip install --no-cache-dir --upgrade "pip>=26.1.2" \
    && python -m pip install --no-cache-dir . \
    && python -m pip uninstall -y pip \
    && chmod 755 /usr/local/bin/normalizer-entrypoint.sh

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/normalizer-entrypoint.sh"]
