# vidtheque-mcp — CPU image, kept multi-arch friendly (it is expected to run on
# a Raspberry Pi as happily as on an x86 box). Nothing here may pull a CUDA
# wheel; if a dependency does, it belongs in the worker instead.
#
# Build from the repo root so the uv workspace is in context:
#
#   docker build -f mcp/Dockerfile -t vidtheque-mcp .
#
# trixie, not bookworm: the base image pins the stdlib's SQLite, and the
# migrations need ≥ 3.44 (ordered aggregates in 0003). trixie ships 3.46.1 —
# the exact version index-schema.md documents; bookworm's 3.40.1 fails every
# clean install at first boot (field report, 2026-08-12). `migrations.migrate`
# asserts the floor with a named error so a future regression says what it is.
FROM python:3.12-slim-trixie

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

# Dependency layer. The trailing rm is not housekeeping: with
# UV_LINK_MODE=copy, uv's wheel cache is a second full copy of the venv, dead
# at runtime, and a layer keeps whatever the RUN that made it leaves behind —
# so it must go in this command or it ships (field report follow-up,
# 2026-08-12; the worker's Dockerfile carries the measured numbers).
COPY pyproject.toml uv.lock .python-version ./
COPY mcp/pyproject.toml mcp/
COPY worker/pyproject.toml worker/
RUN uv sync --locked --no-install-project --package vidtheque-mcp \
    && rm -rf /root/.cache/uv

# Source layer. Only the mcp package: the boundary rule says this image never
# needs worker code.
COPY mcp/ mcp/
RUN uv sync --locked --package vidtheque-mcp \
    && rm -rf /root/.cache/uv

# Not root. This is the process the internet talks to, and it runs yt-dlp and
# ffmpeg over remote input when indexing is enabled. /data is the only thing it
# writes. (2026-08-10 audit, F-28.)
#
# /app stays root-owned, deliberately: the process has no business rewriting
# its own code or venv, and `chown -R /app` over a lower layer copies every
# file it touches into a fresh layer — the worker's Dockerfile carries the
# measured cost. Non-recursive chown over one empty directory only.
RUN groupadd --system --gid 10001 vidtheque \
    && useradd --system --uid 10001 --gid vidtheque --no-create-home vidtheque \
    && mkdir -p /data \
    && chown vidtheque:vidtheque /data
USER vidtheque:vidtheque

VOLUME ["/data"]
EXPOSE 8080

# The corpus is one SQLite file plus one directory of JPEGs, both under /data.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD curl -fsS http://localhost:8080/healthz || exit 1

ENV VIDTHEQUE_HOST=0.0.0.0 \
    VIDTHEQUE_PORT=8080

CMD ["vidtheque-mcp"]
