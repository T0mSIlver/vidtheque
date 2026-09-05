# vidtheque-web — the Next.js front end (docs/design/frontend-migration.md).
#
# The context is `web/`, not the repo root, because this app is self-contained:
#
#   docker build -f web/Dockerfile -t vidtheque-web web
#
# (`mcp/Dockerfile` builds from the root because the uv workspace lives there.
# Nothing in `next build` reads a file above this directory — `tokens:check` is
# the one script that does, and it is a CI check, not a build step.)
#
# Node is 24.18.0, the version .github/workflows/ci-web.yml installs, so the
# image is built by the runtime that runs the checks. The base is pinned by
# DIGEST as well as by tag: a tag is a name the registry may repoint, and
# deterministic builds are the rule the CUDA base and the committed lockfile
# already follow.
#
# TWO RUNTIME ENVIRONMENT VARIABLES, both passed in and neither baked:
#
#   VIDTHEQUE_API_URL           where this server makes its own reads (search,
#                               videos, meta). Under compose that is the
#                               service name, http://mcp:8080 — the browser
#                               never sees it, because browsers reach Python
#                               through the edge by path.
#   VIDTHEQUE_CLIENT_IP_HEADER  the header those reads carry the visitor's
#                               address under. It must equal the instance's
#                               VIDTHEQUE_TRUSTED_IP_HEADER or every visitor
#                               shares one rate-limit bucket
#                               (frontend-migration.md §1c).
#
# There is no build-time configuration and no NEXT_PUBLIC_* anywhere in the
# app, so this image holds no secret and is the same image for every
# deployment. Its output is a server, not a bundle: `app/layout.tsx` calls
# `connection()` so every document renders per request with a fresh CSP nonce
# (§1b), which is also why nothing here prerenders and why no API needs to be
# reachable while building.

FROM node:24.18.0-trixie-slim@sha256:ae91dcc111a68c9d2d81ff2a17bda61be126426176fde6fe7d08ab13b7f50573 AS base

ENV PNPM_HOME=/pnpm \
    PATH=/pnpm:$PATH \
    COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
    NEXT_TELEMETRY_DISABLED=1
# corepack, not `npm i -g pnpm`: `packageManager` in package.json is then the
# one place the version is written, exactly as ci-web.yml arranges it by
# pointing pnpm/action-setup at that file.
RUN corepack enable
WORKDIR /app

# Dependency layer, keyed on the lockfile alone so a source edit does not
# reinstall. `--frozen-lockfile` is the same flag CI installs with: the lockfile
# is the input, never a thing the build is allowed to update.
FROM base AS deps
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

FROM base AS build
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN pnpm build

# Runtime. `output: "standalone"` in next.config.ts means the build traces the
# server's actual imports, so this stage carries that bundle instead of the
# whole node_modules tree — and the dev dependencies (eslint, vitest, the
# testing-library set) are not in the image at all rather than being pruned
# out of it afterwards.
FROM base AS runtime

ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# `node` (uid 1000) ships in the base image, so this is a non-root runtime
# without a useradd or a recursive chown. /app stays root-owned for the reason
# mcp/Dockerfile gives: the process has no business rewriting its own code.
# The one exception is the incremental cache — `src/lib/library.ts` keeps the
# two library reads in `unstable_cache`, whose file-system handler writes under
# .next/cache — so that directory, and only that directory, is the server's.
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
RUN mkdir -p /app/.next/cache && chown node:node /app/.next/cache

USER node:node
EXPOSE 3000

# No HEALTHCHECK. This app has no endpoint of its own — `/healthz` is Python's
# (§1a) — and every page it serves is a per-request render that reads the API,
# so a probe would be a page load charged to the instance every 30 s. The edge
# checks `/healthz` through Caddy instead (deploy/Caddyfile).

CMD ["node", "server.js"]
