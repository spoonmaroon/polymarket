# Barebones Setup

## GitHub

Private repo:

```text
git@github.com:AnimeWeeb9000/polymarket.git
```

## Python

Create or refresh the local environment:

```bash
uv sync --dev
```

Run the API:

```bash
uv run uvicorn polymarket_engine.app:app --reload
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

## C++

Configure and build:

```bash
cmake -S . -B cmake-build-debug
cmake --build cmake-build-debug
```

## UI

Install UI dependencies:

```bash
cd ui
npm install
npm run dev
```

## Secrets

Copy `.env.example` to `.env` and fill values locally. Do not commit `.env`.
