# No real default: the tag is computed. A plain `docker build` fails on this name, which says what to run.
ARG BASE_IMAGE=vidtheque-mcp-base:run-make-images-first
FROM ${BASE_IMAGE}

USER root

COPY mcp/ mcp/

# --no-deps installs only this project and leaves the base environment intact.
RUN uv pip install --no-deps ./mcp \
    && rm -rf /root/.cache/uv

USER vidtheque:vidtheque

VOLUME ["/data"]
EXPOSE 8080

# The corpus is one SQLite file plus one directory of JPEGs, both under /data.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD curl -fsS http://localhost:8080/healthz || exit 1

ENV VIDTHEQUE_HOST=0.0.0.0 \
    VIDTHEQUE_PORT=8080

CMD ["vidtheque-mcp"]
