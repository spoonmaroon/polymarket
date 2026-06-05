FROM nvidia/cuda:13.2.1-cudnn-devel-ubuntu24.04

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates python3 python3-pip tini \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
COPY deploy/gpu/gpu-probability-entrypoint.sh /usr/local/bin/gpu-probability-entrypoint.sh

RUN python3 -m pip install --break-system-packages --no-cache-dir . cupy-cuda13x \
    && chmod 755 /usr/local/bin/gpu-probability-entrypoint.sh

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/gpu-probability-entrypoint.sh"]
