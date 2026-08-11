# vidtheque-worker — CUDA runtime image.
#
# Build from the repo root so the uv workspace (root pyproject + uv.lock + both
# members) is in context:
#
#   docker build -f worker/Dockerfile -t vidtheque-worker .
#
# TODO(pin): replace the tag below with the digest of the image you tested
# against, e.g.
#   FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04@sha256:<64-hex-digest>
# Deterministic builds are a project goal; a floating tag silently changes
# cuDNN under ctranslate2, which is exactly the kind of drift that makes a
# benchmark meaningless. Get the digest with:
#   docker buildx imagetools inspect nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04
ARG CUDA_IMAGE=nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04
FROM ${CUDA_IMAGE}

ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.11.24

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    HF_HOME=/hf-cache \
    PATH=/app/.venv/bin:/root/.local/bin:$PATH

# ffmpeg is not optional: whisperX decodes audio through it. libgl1 and
# libglib2.0-0 are not optional either: rapidocr hard-depends on opencv-python
# (not -headless), which fails at import with `libGL.so.1: cannot open shared
# object file` without them.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11.24 /uv /uvx /usr/local/bin/

WORKDIR /app

# Dependency layer: only manifests, so it caches across source edits.
COPY pyproject.toml uv.lock .python-version ./
COPY worker/pyproject.toml worker/
COPY mcp/pyproject.toml mcp/
RUN uv python install ${PYTHON_VERSION} \
    && uv sync --locked --no-install-project --package vidtheque-worker --extra gpu --extra nvml

# Source layer.
COPY worker/ worker/
COPY mcp/src/ mcp/src/
RUN uv sync --locked --package vidtheque-worker --extra gpu --extra nvml

# Not root. This process loads model weights downloaded at runtime and decodes
# untrusted media, which is the pair of properties that makes "container root"
# worth avoiding: a bug in either reaches everything the user can touch, and as
# root that is the whole filesystem, the HF cache it would poison for every
# later load, and the environment. It does not need to write anywhere except
# the cache and /tmp, so it owns those and nothing else.
# (2026-08-10 audit, F-28.)
RUN groupadd --system --gid 10001 vidtheque \
    && useradd --system --uid 10001 --gid vidtheque --no-create-home vidtheque \
    && mkdir -p /hf-cache \
    && chown -R vidtheque:vidtheque /hf-cache /app
USER vidtheque:vidtheque

EXPOSE 8081

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8081/healthz || exit 1

CMD ["vidtheque-worker"]
