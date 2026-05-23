# financial-analyst — multi-agent A-share research workstation
#
# Build:
#   docker build -t financial-analyst .                        # CLI only
#   docker build -t financial-analyst:serve --target serve .   # CLI + SSE bridge
#
# Run (CLI):
#   docker run -it --rm \
#       -e TUSHARE_TOKEN=... -e DASHSCOPE_API_KEY=... \
#       -v $(pwd)/out:/app/out \
#       -v $(pwd)/memories:/app/memories \
#       financial-analyst report SH600519
#
# Run (SSE bridge, for GuanLan UI):
#   docker run -d --rm -p 9999:9999 \
#       -e DASHSCOPE_API_KEY=... \
#       -v $(pwd)/memories:/app/memories \
#       -v ~/.financial-analyst/data:/data/cn_data:ro \
#       financial-analyst:serve

# ────────── Stage 1: base (CLI) ──────────
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

WORKDIR /app

# System deps for lightgbm + pyarrow + curl (healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libgomp1 git curl \
 && rm -rf /var/lib/apt/lists/*

# Install Python deps first (better layer caching: code changes don't bust pip cache)
COPY pyproject.toml README.md LICENSE ./
COPY src/financial_analyst/__init__.py src/financial_analyst/__init__.py
RUN pip install --upgrade pip && \
    pip install -e .

# Copy the rest
COPY src/ src/
COPY config/ config/
COPY memories/ memories/
COPY docs/ docs/
COPY CHANGELOG.md ./

# Create dirs the user typically mounts over (so empty volumes don't break runs)
RUN mkdir -p out news f10 kb /data

# Default: financial-analyst CLI. Override with subcommand.
ENTRYPOINT ["financial-analyst"]
CMD []

# ────────── Stage 2: serve (CLI + fastapi/uvicorn) ──────────
FROM base AS serve

# Install [serve] extras (fastapi + uvicorn)
RUN pip install -e .[serve]

# Healthcheck — SSE bridge /health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:9999/health || exit 1

EXPOSE 9999

# Default to serve mode — override CMD to switch to CLI
CMD ["serve", "--host", "0.0.0.0", "--port", "9999"]
