# vidtheque-mcp — CPU image, kept multi-arch friendly (it is expected to run on
# a Raspberry Pi as happily as on an x86 box). Nothing here may pull a CUDA
# wheel; if a dependency does, it belongs in the worker instead.
#
# Build from the repo root so the uv workspace is in context:
#
#   docker build -f mcp/Dockerfile -t vidtheque-mcp .
#
# NOTE: the MCP framework is not chosen yet (see mcp/NOTE.md), so this image
# currently packages a stub. The CMD is a placeholder that keeps the container
# honest — it says what is missing instead of pretending to serve.
FROM python:3.12-slim-bookworm

ARG UV_VERSION=0.11.24

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    VIDTHEQUE_DATA_DIR=/data \
    PATH=/app/.venv/bin:$PATH

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11.24 /uv /uvx /usr/local/bin/

WORKDIR /app

# Dependency layer.
COPY pyproject.toml uv.lock .python-version ./
COPY mcp/pyproject.toml mcp/
COPY worker/pyproject.toml worker/
RUN uv sync --locked --no-install-project --package vidtheque-mcp

# Source layer. Only the mcp package: the boundary rule says this image never
# needs worker code.
COPY mcp/ mcp/
RUN uv sync --locked --package vidtheque-mcp

VOLUME ["/data"]
EXPOSE 8080

CMD ["python", "-c", "import vidtheque_mcp, sys; \
sys.exit('vidtheque-mcp is a placeholder: the MCP framework has not been chosen yet (see mcp/NOTE.md)')"]
