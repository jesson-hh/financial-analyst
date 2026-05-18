# financial-analyst — multi-agent A-share research workstation
# Build: docker build -t financial-analyst .
# Run:   docker run -it --rm \
#          -e TUSHARE_TOKEN=... -e DASHSCOPE_API_KEY=... \
#          -v $(pwd)/out:/app/out \
#          financial-analyst

FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps for lightgbm + pyarrow (parquet)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libgomp1 git \
 && rm -rf /var/lib/apt/lists/*

# Install Python deps first (better layer caching)
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

# Create directories user will mount over
RUN mkdir -p out news f10 kb

# Default: interactive TUI. Override with `financial-analyst report SH600519` etc.
ENTRYPOINT ["financial-analyst"]
CMD []
