# No real default: the tag is computed. A plain `docker build` fails on this name, which says what to run.
ARG BASE_IMAGE=vidtheque-worker-base:run-make-images-first
FROM ${BASE_IMAGE}

USER root

COPY worker/ worker/

# --no-deps installs only this project and leaves the base environment intact.
RUN uv pip install --no-deps ./worker \
    && rm -rf /root/.cache/uv

USER vidtheque:vidtheque

EXPOSE 8081

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8081/healthz || exit 1

CMD ["vidtheque-worker"]
