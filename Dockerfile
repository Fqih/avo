# Multi-stage Dockerfile for Avo CLI and Agent Runtime
# Stage 1: Build virtual environment with runtime dependencies
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir ".[all]"

# Stage 2: Minimal, secure runtime
FROM python:3.12-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create non-root operator user
RUN useradd -m -u 1000 -s /bin/bash avo \
    && mkdir -p /workspace /home/avo/.avo /home/avo/.config/avo \
    && chown -R avo:avo /workspace /home/avo

COPY --from=builder /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED="1" \
    AVO_DATABASE_PATH="/home/avo/.avo/avo.db"

USER avo
WORKDIR /workspace

ENTRYPOINT ["avo"]
CMD ["chat"]
