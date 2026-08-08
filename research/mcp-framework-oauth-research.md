# Remote MCP server in Python — framework + OAuth research (2026-08-08)

Research for vidtheque's `mcp/` image: which Python MCP framework to build on, how to do
spec-compliant OAuth that claude.ai actually accepts, how to serve authenticated non-MCP
endpoints (`/frames/<id>.jpg`) in the same process, what to persist, and how to switch auth off
for LAN self-hosters.

All version facts checked 2026-08-08 against PyPI, the GitHub trees, and vendor docs.

---

## 1. The landscape as of 2026-08-08

### 1.1 The spec moved twice since the "MCP OAuth" blog posts everyone links to

- **2025-11-25** — the revision most tutorials describe (RFC 9728 protected-resource metadata, DCR,
  RFC 8707 resource indicators).
- **2026-07-28** — current. Two changes that matter to us:
  - **The protocol went sessionless.** No `Mcp-Session-Id` on the modern path; every request
    carries its protocol version, client info and capabilities in `_meta`. Any replica can answer
    any request. ([spec blog](https://blog.modelcontextprotocol.io/posts/2026-07-28/),
    [flaviocopes writeup](https://flaviocopes.com/mcp-2026-07-28-stateless/))
  - **DCR is formally deprecated in favour of CIMD** (Client ID Metadata Documents,
    `draft-ietf-oauth-client-id-metadata-document-00`). DCR still works "for backwards
    compatibility with authorization servers that do not support Client ID Metadata Documents" and
    "will be removed in a future version".
    ([authorization spec](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization))

Normative bits we must satisfy as a server (from the 2026-07-28 authorization page):

| Requirement | Level |
|---|---|
| MCP server implements RFC 9728 Protected Resource Metadata | **MUST** |
| AS provides RFC 8414 *or* OIDC Discovery metadata | **MUST** (at least one) |
| AS + clients support CIMD | **SHOULD** |
| AS + clients support DCR (RFC 7591) | **MAY** (deprecated) |
| Validate token audience — token was issued *for us* (RFC 8707) | **MUST** |
| 401 on invalid/absent token; 403 + `error="insufficient_scope"` for scope step-up | **MUST/SHOULD** |
| `scope` parameter in the `WWW-Authenticate` challenge | **SHOULD** |
| AS emits `iss` in authorization responses (RFC 9207) + advertises `authorization_response_iss_parameter_supported` | **SHOULD** |
| PRM `scopes_supported` **SHOULD NOT** include `offline_access` | **SHOULD NOT** |

Note the last one interacts with Claude's behaviour — see §2.2.

### 1.2 Official SDK: `mcp` 2.0.0 — and `FastMCP` was renamed

**`mcp==2.0.0` is stable on PyPI** (released alongside the 2026-07-28 spec; v1.x is now
security-fixes-only at `1.29.0`).
([releases](https://github.com/modelcontextprotocol/python-sdk/releases),
[what's new](https://py.sdk.modelcontextprotocol.io/whats-new/))

The headline breaking change: **the SDK's `FastMCP` class is now `MCPServer`.**

```python
# v1
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("Demo")

# v2
from mcp.server import MCPServer
mcp = MCPServer("Demo")
```

One `streamable_http_app()` answers *both* protocol revisions — a 2025-era client's `initialize`
and a 2026-era client's stateless request hit the same endpoint with nothing to configure.
Other v2 notes: 4 MiB request-body cap (HTTP 413, raise via `max_request_body_size`), per-request
stream buffering, `httpx2`.

### 1.3 Independent FastMCP: stable line is *behind* the SDK

| Package | Latest | Date | Depends on |
|---|---|---|---|
| `fastmcp` (stable) | **3.4.6** | 2026-08-05 | `fastmcp-slim` → `mcp<2.0,>=1.24.0` |
| `fastmcp` (beta) | **4.0.0b2** | 2026-08-07 | `mcp<3.0,>=2.0.0`, `mcp-types>=2.0` |

That table is the single most important finding of this document. **FastMCP's stable release still
pins the v1 SDK**, i.e. it cannot speak the 2026-07-28 revision. The FastMCP release that does is
4.0, which its own docs describe as: *"FastMCP 4 is in **beta**. Pin an exact version and expect
sharp edges."* ([what's new in FastMCP 4](https://github.com/jlowin/fastmcp/blob/main/docs/getting-started/whats-new.mdx))

The repo also restructured a couple of weeks ago into a uv workspace
(`fastmcp_slim/`, `fastmcp_remote/`, `fastmcp_tasks/`) and the project URL now reads
`https://github.com/PrefectHQ/fastmcp`. Maintenance is *intense*, not stale — ~weekly releases,
v3.4.x still getting fixes. But it is a moving target this month.

Release cadence (from `gh api repos/jlowin/fastmcp/releases`): v4.0.0b2 2026-08-07, v3.4.6
2026-08-05, v4.0.0b1 2026-07-28, v3.4.5 2026-07-27, v3.4.4 2026-07-09, …

### 1.4 Server-side OAuth: what each actually ships

**Official SDK (`mcp` 2.0):** the full authorization-server machinery is present and unremoved —
`mcp/server/auth/{provider,routes,settings}.py`, handlers for `/authorize`, `/token`, `/register`,
`/revoke`, plus `create_auth_routes()` and `create_protected_resource_routes()` which return plain
Starlette `Route` lists. `MCPServer.__init__` takes **either** `auth_server_provider=` (full AS)
**or** `token_verifier=` (RS only), always paired with `auth=AuthSettings(...)`.

But the docs steer hard away from the AS role:

> "There is a second constructor argument, `auth_server_provider=`, that embeds a full
> authorization server inside your MCP server. It predates the AS/RS separation that the MCP
> authorization spec is built around. **New servers should not reach for it.**"
> — [py.sdk.modelcontextprotocol.io/run/authorization](https://py.sdk.modelcontextprotocol.io/run/authorization/)

That is guidance, not a spec constraint: the spec itself says the AS "may be hosted with the
resource server or a separate entity". The practical implication is that we can still *use* the
handlers, we just shouldn't wire them through `auth_server_provider=` and inherit its defaults
(see the metadata gaps in §1.5).

Two known gaps in the SDK's AS metadata builder (`mcp/server/auth/routes.py::build_metadata`):

```python
token_endpoint_auth_methods_supported=["client_secret_post", "client_secret_basic"],
```

- `"none"` is **not** advertised → CIMD is off the table for any client that checks (Claude does).
- `client_id_metadata_document_supported` exists on the `OAuthMetadata` model
  (`mcp/shared/auth.py`) but `build_metadata()` never sets it.

Both are fixable by serving our own `/.well-known/oauth-authorization-server` handler — which is
exactly what FastMCP does (it rebuilds the metadata route to override `issuer`).

**FastMCP:** four documented server-auth patterns
([authentication overview](https://gofastmcp.com/servers/auth/authentication)):

1. `TokenVerifier` / `JWTVerifier` / `StaticTokenVerifier` — validate tokens issued elsewhere.
2. `RemoteAuthProvider` — external IdP that already supports DCR (WorkOS AuthKit, Descope).
3. `OAuthProxy` — bridges a *traditional* OAuth app (GitHub, Google, Azure, AWS) to MCP's
   DCR/CIMD world: presents a DCR-compliant face to clients, uses your pre-registered credentials
   upstream, and **issues its own JWTs** (post-CVE-2025-69196 hardening) with a consent screen.
4. `OAuthProvider` — **full self-contained OAuth server**, an *abstract* class; you implement ~10
   async methods. Docs since 2.11.0 carry a loud warning: *"an extremely advanced pattern that most
   users should avoid… Use Remote OAuth instead unless you have compelling requirements that
   external identity providers cannot meet, such as **air-gapped environments**."*
   ([full-oauth-server](https://gofastmcp.com/servers/auth/full-oauth-server))

There is **no batteries-included self-contained issuer**. The only concrete `OAuthProvider`
subclass in the tree is `fastmcp/server/auth/providers/in_memory.py::InMemoryOAuthProvider`, whose
docstring says *"An in-memory OAuth provider for testing purposes"* — dict-backed clients, codes
and tokens, `test_access_token_<hex>` strings. Useful as a reference implementation, not a
production path.

Bundled *provider* modules (all external IdPs): auth0, aws, azure, clerk, descope, discord,
github, google, huggingface, keycloak, oci, propelauth, scalekit, supabase, workos, plus
`jwt`, `introspection`, `debug`.

**FastMCP's genuinely valuable OAuth extras** (things the official SDK does not have):

- `fastmcp/server/auth/cimd.py` — a real server-side CIMD implementation: `CIMDDocument` pydantic
  model, `CIMDFetcher` with SSRF protection (`ssrf.py`, `redirect_validation.py`), wildcard
  redirect matching for loopback (`http://localhost:*/callback`), `private_key_jwt` client auth via
  `joserfc`. Marked **beta**. **Wired into `OAuthProxy` only** (`enable_cimd: bool = True`), not
  into `OAuthProvider`.
- `fastmcp/server/auth/jwt_issuer.py`, `oauth_proxy/consent.py`, `oauth_proxy/ui.py` — token
  issuance and a consent screen.
- Persistent, encrypted storage by default: `OAuthProxy(client_storage: AsyncKeyValue | None)`
  defaults to a `FileTreeStore` under `settings.home / "oauth-proxy" / <key-fingerprint>` wrapped
  in `FernetEncryptionWrapper`, with refresh tokens **stored by hash only**. Backed by
  [py-key-value](https://strawgate.com/py-key-value/) (memory, disk, FileTree, Redis, Postgres,
  DuckDB, S3, …). See [FastMCP 2.13 writeup](https://jlowin.dev/blog/fastmcp-2-13).

### 1.5 Comparison table

| | Official `mcp` 2.0.0 (`MCPServer`) | FastMCP 3.4.6 (stable) | FastMCP 4.0.0b2 (beta) |
|---|---|---|---|
| Stability | **stable**, released with the spec | stable, mature | **beta**, weekly churn |
| Spec revision | 2026-07-28 **and** 2025-era, one endpoint | 2025-era only (`mcp<2.0`) | both eras |
| Server class | `MCPServer` (renamed from `FastMCP`) | `FastMCP` | `FastMCP` |
| Streamable HTTP | `streamable_http_app()` → Starlette | `http_app()` → Starlette | `http_app()` |
| Mount into Starlette/FastAPI | yes (host app must own the lifespan) | yes (`combine_lifespans`) | yes |
| Unauthenticated custom routes | `@mcp.custom_route` (never authenticated) | `@mcp.custom_route` (never authenticated) | same |
| RS token verification | `TokenVerifier` + `AuthSettings` | `TokenVerifier`/`JWTVerifier`/`MultiAuth` | same + identity assertion |
| PRM (RFC 9728) auto-served | yes | yes | yes |
| Self-contained AS | present but "new servers should not reach for it" | `OAuthProvider` abstract, ~10 methods | same |
| Concrete self-contained AS | ✗ (example only: `examples/servers/simple-auth`) | ✗ (`InMemoryOAuthProvider` = testing) | ✗ |
| Server-side **CIMD** | ✗ (metadata field exists, never set) | ✓ beta, **OAuthProxy only** | ✓ beta, OAuthProxy only |
| Bridge to GitHub/Google/etc. | ✗ | `OAuthProxy` + 15 providers | same |
| Persistent OAuth storage | ✗ (yours) | py-key-value, encrypted FileTree default | same |
| Extra weight | minimal | authlib, cryptography, joserfc, key_value, … | same |

---

## 2. What claude.ai (and the other clients) actually require

Primary source: **[Authentication for connectors](https://claude.com/docs/connectors/building/authentication)**
and **[Lazy authentication](https://claude.com/docs/connectors/building/lazy-authentication)** —
both current, both explicit. Everything below is from those two pages unless noted.

### 2.1 Supported auth types (one infrastructure across Claude.ai, Desktop, mobile, Claude Code, Cowork)

| Type | Availability |
|---|---|
| `oauth_dcr` — OAuth 2.0 + DCR (RFC 7591) | out of the box |
| `oauth_cimd` — OAuth 2.0 + Client ID Metadata Document | out of the box |
| `oauth_anthropic_creds` — Anthropic-held client credentials | email `mcp-review@anthropic.com` |
| `custom_connection` | contact Anthropic |
| `static_headers` — fixed API key / bearer entered by an admin | **beta** |
| **`none` — no authentication (authless server)** | **supported** |

Pure `client_credentials` M2M is **not supported** — "every connection requires user consent".

### 2.2 The exact handshake

1. **Return `401`, never a tool error.** A `200` with `isError: true` is passed to the model as
   text and *no auth prompt appears*. Only a transport-level `401` makes Claude pause, run OAuth,
   and retry. A `403` re-authenticates **only** with `WWW-Authenticate: Bearer
   error="insufficient_scope"`.

   ```http
   HTTP/1.1 401 Unauthorized
   WWW-Authenticate: Bearer error="invalid_token",
                     resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource/mcp",
                     scope="vidtheque:read"
   ```

2. **PRM (RFC 9728).** `resource` **must match the MCP server URL exactly as the user types it into
   Claude**, path included. `authorization_servers` — Claude uses the **first entry only** and does
   not fall back. Fallback probing if no `resource_metadata` pointer:
   `/.well-known/oauth-protected-resource/<mcp-path>` then `/.well-known/oauth-protected-resource`.

3. **AS metadata**: RFC 8414 or OIDC Discovery at the issuer's `/.well-known/` paths; must be
   reachable from Anthropic's egress range **`160.79.104.0/21`** (a WAF in front of the IdP is a
   classic silent failure).

4. **CIMD selection rule** — Claude picks CIMD *only* when the AS metadata advertises **both**:
   ```json
   { "client_id_metadata_document_supported": true,
     "token_endpoint_auth_methods_supported": ["none"] }
   ```
   Missing either → Claude falls back to looking for a `registration_endpoint` (DCR).
   Anthropic explicitly recommends **CIMD or `oauth_anthropic_creds` over DCR** for high-traffic
   servers, because DCR registers a new client on *every fresh connection*.

5. **PKCE S256 always**, on every authorization request, whichever registration mechanism is used.
   Metadata must advertise `"code_challenge_methods_supported": ["S256"]`.

6. **Redirect URIs**
   - Hosted Claude surfaces (web, Desktop, mobile, Cowork): `https://claude.ai/api/mcp/auth_callback`
   - **Claude Code**: RFC 8252 loopback on an ephemeral port. It declares
     `http://localhost/callback` and `http://127.0.0.1/callback` in its CIMD
     (`https://claude.ai/oauth/claude-code-client-metadata`) — **the AS must match both with the
     port component ignored.** Claude Code runs its own flow and does *not* use Anthropic-held creds.

7. **Token endpoint**: must accept `Content-Type: application/x-www-form-urlencoded` (RFC 6749
   §4.1.3). `/register` uses `application/json` (RFC 7591 §3.1) — different parsers.

8. **Refresh**: reactive on 401 + proactive up to 5 min before expiry. Return `invalid_grant` (not
   a custom code) for dead refresh tokens. **Rotate refresh tokens** — DCR and CIMD both register
   Claude as a *public* client, and OAuth 2.1 requires rotation or sender-constraining for those.

9. **`offline_access` subtlety.** Claude appends `offline_access` to the requested scopes *only if
   the **authorization server** metadata lists it in `scopes_supported`*. Meanwhile the MCP spec
   says the **protected resource** SHOULD NOT list it. So: put `offline_access` in the AS metadata's
   `scopes_supported`, keep it out of the PRM's. Get this wrong and you get no refresh token.

10. **Latency budget**: 10 s for discovery / registration / token, 30 s for refresh. Discovery
    documents are cached **globally, keyed by URL**, ~5 min staleness.

### 2.3 Authless (`none`)

Officially supported for individually-added custom connectors. **Caveat**: the *org-managed*
(Team/Enterprise admin) connector flow assumes OAuth 2.1 and attempts DCR, and there is no "no
auth" option in that admin UI — [claude-ai-mcp#402](https://github.com/anthropics/claude-ai-mcp/issues/402),
**closed as not-planned**, workaround is the `mcp-remote` stdio bridge. Irrelevant for our
self-hoster persona, worth a README line.

### 2.4 Static header auth (the cheap path)

`static_headers` is in beta: the admin enters e.g. `Authorization: Bearer <token>` once in the
*Add custom connector* dialog, and Claude sends it on every request. Header names come from an
allowlist (`authorization`, `x-api-key`, `x-auth-token`, …), max four, value sent **verbatim**
(you must type `Bearer ` yourself). Shared per-organization, not per-user.
([remote-mcp](https://claude.com/docs/connectors/custom/remote-mcp))

Claude Code supports the same thing today without any beta flag:
`claude mcp add --transport http vidtheque https://… --header "Authorization: Bearer …"`.
`--transport http` is the only remote transport that supports OAuth; `/mcp` in-session drives the
interactive sign-in. ([Claude Code MCP docs](https://code.claude.com/docs/en/mcp))

### 2.5 Other clients

- **Cursor** — remote MCP over OAuth; discovers the AS and registers via **DCR or CIMD**, then
  opens a browser. ([truefoundry](https://www.truefoundry.com/blog/mcp-authentication-in-cursor-oauth-api-keys-and-secure-configuration))
- **VS Code / Copilot** — handles the OAuth flow for remote servers and **supports CIMD today**;
  `copilot-cli` has an open request for parity ([copilot-cli#1305](https://github.com/github/copilot-cli/issues/1305)).
- Net: **advertise CIMD *and* keep DCR enabled.** CIMD alone is not yet universal; DCR alone
  contradicts where the spec is going and makes Claude create a client per connection.

---

## 3. Serving plain HTTP alongside MCP

### 3.1 Both frameworks say the same thing: your app is the root, MCP is a mount

Official SDK ([Add to an existing app](https://py.sdk.modelcontextprotocol.io/run/asgi/)):

```python
@asynccontextmanager
async def lifespan(app: Starlette) -> AsyncIterator[None]:
    async with mcp.session_manager.run():
        yield

app = Starlette(
    routes=[..., Mount("/", app=mcp.streamable_http_app())],
    lifespan=lifespan,
)
```

Three gotchas, all documented, all real:

- **A mounted sub-app's lifespan never runs.** The top-level app must enter
  `mcp.session_manager.run()` itself or the first request dies with
  `RuntimeError: Task group is not initialized.`
- `mcp.session_manager` only exists **after** `streamable_http_app()` has been called.
- `Mount("/")` matches everything — our own routes must be listed **before** it.

FastMCP equivalent: `mcp.http_app(path="/mcp")`, mount into Starlette passing `lifespan=mcp_app.lifespan`,
or into FastAPI with `combine_lifespans(app_lifespan, mcp_app.lifespan)`.

### 3.2 Custom routes are never authenticated — in *both* frameworks

> "Custom routes are **never authenticated**, even when the rest of the server is. That is
> deliberate: health checks and OAuth callbacks have to be reachable before any token exists.
> Don't put anything private behind one." — SDK ASGI docs

FastMCP says the same and adds the remedy: *"If you need authenticated HTTP endpoints alongside
your MCP server, mount it in a FastAPI app and use FastAPI's `Depends()` for auth on your routes."*

**So `/frames/<id>.jpg` cannot be a `@custom_route`.** It has to be a route on our own app, with
our own auth dependency. This flattens the framework decision considerably: either way we own a
Starlette/FastAPI root app, and the MCP framework is "just" the tool-decorator layer.

### 3.3 DNS-rebinding / Host allowlist — the tunnel footgun

`streamable_http_app()` arms DNS-rebinding protection with a localhost-only allowlist. Behind a
real hostname (i.e. our cloudflared tunnel) **every request is rejected with `421 Misdirected
Request`** until `transport_security=TransportSecuritySettings(allowed_hosts=[...])` names it.
FastMCP's equivalent is `host_origin_protection` / `allowed_hosts` / `allowed_origins`
(env: `FASTMCP_HTTP_ALLOWED_HOSTS`). Whatever we build, `PUBLIC_URL` must feed this.

### 3.4 Can a tool result reference a URL the user opens in a browser?

Constraints, in order of how much they hurt:

- **Base64 `ImageContent` is a dead end for Claude Code.**
  [claude-code#31208](https://github.com/anthropics/claude-code/issues/31208) — *"MCP ImageContent
  returned as text in tool results instead of native image blocks (10-20x token waste)"*, opened
  2026-03-05, **closed as not-planned**. Confirms the handoff's premise for `get_frames(return="url")`.
- **claude.ai/Desktop don't render tool-result images inline either.** The model can see a base64
  image and reason about it, but it is never rendered in the assistant response
  ([claude-ai-mcp#238](https://github.com/anthropics/claude-ai-mcp/issues/238),
  [anthropic-sdk-python#1329](https://github.com/anthropics/anthropic-sdk-python/issues/1329),
  [claude-code#53256](https://github.com/anthropics/claude-code/issues/53256)). The suggested
  alternative in those threads is exactly ours: *present a URL to the user*.
- **A browser has no `Authorization` header.** When the human clicks
  `https://vidtheque.example.com/frames/abc.jpg`, no OAuth bearer is attached. Three options:

  | Option | Works in browser | Notes |
  |---|---|---|
  | **HMAC-signed, short-TTL URL** (`?exp=…&sig=…`) | ✓ | Standard capability-URL pattern; recommended. Treat as a bearer token — short TTL, server-side signing key |
  | Session cookie set during the OAuth login | ✓ *if* the user logged in on that origin | Our AS login page is same-origin, so this is plausible; breaks for a user who authorised on another device |
  | Bearer only | ✗ for humans | Fine for programmatic fetches; keep it accepted in addition |

  Note the spec's *"access tokens MUST NOT be included in the URI query string"* applies to MCP
  requests with OAuth access tokens — a separate, short-lived, single-purpose HMAC signature on an
  image path is a different thing, but it is still a secret in a URL, so keep the TTL tight and say
  so in the docs.
- **Size**: Claude Desktop has a ~1 MB hard limit on file content returned by a tool or resource —
  another argument for URLs over inline bytes for keyframes.

---

## 4. Persistence

Neither framework will hand us a schema.

- **Official SDK**: zero storage. `OAuthAuthorizationServerProvider` is a protocol with
  `get_client` / `register_client` / `authorize` / `load_authorization_code` /
  `exchange_authorization_code` / `load_refresh_token` / `exchange_refresh_token` /
  `load_access_token` / `revoke_token` / `verify_token`. Storage is 100 % ours.
- **FastMCP**: same abstract surface for `OAuthProvider`; storage only comes for free on the
  **`OAuthProxy`** path, via `py-key-value` (`AsyncKeyValue`). Backends include memory, Disk,
  FileTree, Redis, Postgres, DuckDB, chDB, S3, … — **no first-class SQLite store**, though DuckDB
  and Postgres are there and the Disk/FileTree stores are local-filesystem. Default is FileTree +
  Fernet encryption under `settings.home`, refresh tokens keyed by hash.

Given we are already committed to SQLite + sqlite-vec for the corpus, hand-rolling the auth tables
is a few dozen lines and keeps the deployment to one runtime dependency-free store. Proposed
schema (separate file from the corpus DB — see open question 2):

```sql
-- auth.db  (WAL)
CREATE TABLE oauth_clients (          -- DCR registrations only; CIMD clients are never stored
  client_id TEXT PRIMARY KEY,
  client_secret_hash TEXT,            -- NULL for public clients (token_endpoint_auth_method=none)
  metadata_json TEXT NOT NULL,        -- OAuthClientInformationFull
  created_at INTEGER NOT NULL,
  last_seen_at INTEGER
);
CREATE TABLE auth_codes (             -- 5 min TTL, single use
  code TEXT PRIMARY KEY, client_id TEXT NOT NULL, redirect_uri TEXT NOT NULL,
  code_challenge TEXT NOT NULL, scopes TEXT NOT NULL, resource TEXT,
  subject TEXT NOT NULL, expires_at INTEGER NOT NULL
);
CREATE TABLE refresh_tokens (         -- sha256 hash only, rotating
  token_hash TEXT PRIMARY KEY, client_id TEXT NOT NULL, subject TEXT NOT NULL,
  scopes TEXT NOT NULL, resource TEXT, issued_at INTEGER NOT NULL,
  expires_at INTEGER, rotated_to TEXT, revoked_at INTEGER
);
CREATE TABLE login_sessions (         -- browser cookie for the AS login/consent page
  sid TEXT PRIMARY KEY, subject TEXT NOT NULL, expires_at INTEGER NOT NULL
);
```

**Access tokens should be stateless JWTs** (HS256 over a server secret; `iss` = our issuer,
`aud` = the PRM `resource` value, `sub` = owner, `scope`, `exp`, `jti`) — no access-token table, no
DB read on the hot path, and `TokenVerifier` becomes a signature + `aud` + `exp` check. This is the
shape FastMCP's `OAuthProxy` settled on too (HS256, minimal claims: iss/aud/client_id/scopes/jti).
Signing key derived from a single `VIDTHEQUE_SECRET` (auto-generated into the data dir on first
boot if unset), so a compose file needs no secret management.

---

## 5. Auth mode `none`

Clean in both frameworks — it is simply *not passing* the auth arguments:

- **SDK**: omit `auth=` and `token_verifier=`. Passing one without the other raises `ValueError` at
  construction, so a config bug fails at boot, not at request time. With `auth=None` no PRM route is
  registered and no 401 is ever emitted, which is exactly what an authless connector needs (Claude
  probes the well-known paths, gets 404, connects anonymously).
- **FastMCP**: `FastMCP("vidtheque", auth=None)`.

Recommended three-mode env switch (see §6.3): `none` → `token` → `oauth`. The important design rule
is that **the mode must be one branch at app-construction time**, not per-route conditionals
sprinkled through the codebase.

---

## 6. RECOMMENDATION

### 6.1 Framework: official `mcp` SDK v2, `MCPServer`

**Pin `mcp>=2.0,<3.0` (develop against `2.0.0`).**

Reasons, in order:

1. **It is the only stable Python option that speaks the 2026-07-28 revision.** FastMCP stable
   (3.4.6) pins `mcp<2.0`; the FastMCP that speaks it is a beta that restructured its repo last
   week. Shipping a self-hostable product on a beta whose own docs say "expect sharp edges" is the
   wrong trade for a thing other people will `docker compose up` and forget about.
2. **We own the ASGI root app either way.** Both frameworks refuse to authenticate custom routes,
   and `/frames/<id>.jpg` must be authenticated. So the app is a Starlette app with our routes plus
   a `Mount` for MCP — and the framework's job shrinks to tool registration and the streamable-HTTP
   transport, where the two are equivalent.
3. **We are writing the authorization server ourselves regardless** (§6.2). FastMCP's OAuth value —
   `OAuthProxy`, 15 IdP providers, encrypted key-value storage — is all on the *external IdP* path
   we are deliberately not taking. Its `OAuthProvider` is the same abstract 10-method exercise as
   the SDK's protocol.
4. **Dependency weight.** FastMCP pulls authlib, cryptography, joserfc, key_value, beartype,
   opentelemetry. The mcp image is multi-arch and runs on a Pi.
5. **Legibility.** A self-hosted MCP server whose auth story is "the official SDK plus ~400 lines
   of documented OAuth" is easier for contributors and for the blog post than one whose auth is
   distributed across a third-party framework's abstractions.

**What we give up**, honestly: FastMCP's middleware system, tool transformation, `MultiAuth`,
server composition/proxying, the `fastmcp` CLI, and its beta CIMD implementation (which we will
re-implement — ~150 lines, see 6.2). If Tom would rather have those, the fallback is FastMCP
`4.0.0b2` pinned exactly, revisited at 4.0 stable — see open question 6.

### 6.2 Auth architecture: self-contained issuer, CIMD-first, DCR retained

vidtheque **is** its own authorization server. No Auth0, no WorkOS, no Keycloak container in the
compose file. The spec permits it ("[the AS] may be hosted with the resource server"); Anthropic's
own worked example in [lazy authentication](https://claude.com/docs/connectors/building/lazy-authentication)
is a single-file server that is its own AS.

Implementation: **reuse the SDK's OAuth handlers, bypass `auth_server_provider=`.**

- Implement `VidthequeOAuthProvider(OAuthAuthorizationServerProvider)` — the 10 methods, SQLite-backed.
- Mount `create_auth_routes(provider, ...)` for `/authorize`, `/token`, `/register`, `/revoke` —
  battle-tested PKCE, form parsing, error shapes, RFC 9207 `iss`.
- **Serve our own `/.well-known/oauth-authorization-server`** instead of the SDK's, because
  `build_metadata()` omits `client_id_metadata_document_supported` and never advertises `"none"`.
  Required document:

  ```json
  {
    "issuer": "https://vidtheque.example.com",
    "authorization_endpoint": "https://vidtheque.example.com/authorize",
    "token_endpoint": "https://vidtheque.example.com/token",
    "registration_endpoint": "https://vidtheque.example.com/register",
    "revocation_endpoint": "https://vidtheque.example.com/revoke",
    "scopes_supported": ["vidtheque:read", "vidtheque:write", "offline_access"],
    "response_types_supported": ["code"],
    "grant_types_supported": ["authorization_code", "refresh_token"],
    "token_endpoint_auth_methods_supported": ["none"],
    "code_challenge_methods_supported": ["S256"],
    "client_id_metadata_document_supported": true,
    "authorization_response_iss_parameter_supported": true
  }
  ```
  (`offline_access` in the **AS** metadata only — never in the PRM. §2.2 point 9.)
- **CIMD inside `get_client()`**: when `client_id` is an `https://` URL, fetch it (SSRF-guarded,
  cached), assert the document is self-referential (`doc.client_id == fetched URL`), require
  `token_endpoint_auth_method` ∈ {`none`, `private_key_jwt`}, validate `redirect_uris` are
  same-origin with the `client_id` URL (loopback exempt), and synthesise an
  `OAuthClientInformationFull` in memory. Nothing is persisted. Loopback comparison **ignores the
  port** for `127.0.0.1`, `[::1]` *and* `localhost` (Claude Code needs the last one). Consent screen
  displays the **host of the `client_id` URL**, not `client_name` — the document is self-asserted.
  Port FastMCP's `cimd.py` + `ssrf.py` as the reference (Apache-2.0, compatible with our MIT).
- **DCR stays enabled** for clients that don't do CIMD yet.
- Access tokens = HS256 JWTs (§4); refresh tokens rotate, stored hashed.
- **User authentication** = one owner. `GET /auth/login` renders a minimal password form; the
  password comes from `VIDTHEQUE_PASSWORD` (or a pairing code printed to the container log on first
  boot — open question 3). Consent screen on first authorization per client.
- `/frames/<id>.jpg` accepts, in order: HMAC signature (`?exp=&sig=`), `Authorization: Bearer`,
  login-session cookie. Signature key derived from `VIDTHEQUE_SECRET` with a distinct salt.

### 6.3 Three auth modes

| `VIDTHEQUE_AUTH` | What runs | Who it's for |
|---|---|---|
| `none` | no `AuthSettings`, no PRM, no 401; frames served unsigned | localhost / trusted LAN; `claude mcp add --transport http … http://pi.local:8000/mcp` |
| `token` | `StaticTokenVerifier` over `VIDTHEQUE_TOKEN`; frames need signature or bearer | tunnel without OAuth; Claude Code `--header`, Cursor, claude.ai `static_headers` (beta) |
| `oauth` | full self-contained AS per §6.2 | claude.ai custom connector, the documented default for public exposure |

### 6.4 Pinned versions

```toml
# mcp/pyproject.toml
requires-python = ">=3.11"
dependencies = [
  "mcp>=2.0.0,<3.0.0",          # official SDK v2 — MCPServer, 2026-07-28 + legacy
  "starlette>=1.0.1",
  "uvicorn[standard]>=0.35",
  "pyjwt[crypto]>=2.10",        # HS256 issuance/verification (or joserfc, if we port FastMCP's CIMD)
  "httpx>=0.28",                # CIMD document fetch
  "pydantic>=2.11",
  # corpus side: sqlite-vec, yt-dlp, …
]
```

Deliberately **not** depending on `fastmcp`. Revisit at FastMCP 4.0 stable.

### 6.5 Architecture sketch

```
mcp/
├─ vidtheque/
│  ├─ app.py              # build_app(settings) -> Starlette. Routes in order, then Mount("/", mcp_app).
│  │                      # lifespan: mcp.session_manager.run() + db pool + job queue
│  ├─ config.py           # PUBLIC_URL (required when auth != none), AUTH mode, SECRET, DATA_DIR, WORKER_URL
│  ├─ server.py           # MCPServer("vidtheque"); @mcp.tool search / get_frames / index_video / …
│  ├─ auth/
│  │  ├─ modes.py         # none | token | oauth  -> (token_verifier, extra_routes)
│  │  ├─ provider.py      # VidthequeOAuthProvider(OAuthAuthorizationServerProvider)
│  │  ├─ cimd.py          # CIMD fetch/validate (SSRF guard, loopback port-agnostic match)
│  │  ├─ jwt.py           # issue/verify HS256 access tokens; sign/verify frame URLs
│  │  ├─ metadata.py      # our /.well-known/oauth-authorization-server (CIMD + "none")
│  │  ├─ login.py         # GET/POST /auth/login, consent screen
│  │  └─ store.py         # SQLite: clients, codes, refresh tokens, login sessions
│  ├─ http/
│  │  ├─ frames.py        # GET /frames/{frame_id}.jpg  (sig | bearer | cookie)
│  │  └─ health.py        # GET /healthz  (always public)
│  ├─ index/ …            # sqlite-vec + FTS5, per the tool-surface doc
│  └─ jobs/ …
└─ tests/
```

Route table (mode `oauth`):

```
GET    /healthz                                              public
GET    /.well-known/oauth-protected-resource                 public   RFC 9728, resource == PUBLIC_URL + /mcp
GET    /.well-known/oauth-protected-resource/mcp             public   path-suffixed variant (Claude tries this first)
GET    /.well-known/oauth-authorization-server               public   RFC 8414, ours not the SDK's
GET,POST /authorize                                          public   SDK handler + our login/consent
POST   /token                                                public   SDK handler (form-urlencoded!)
POST   /register                                             public   SDK handler (JSON!), DCR fallback
POST   /revoke                                               public   SDK handler
GET,POST /auth/login                                         public   owner password -> login_sessions cookie
GET    /frames/{frame_id}.jpg                                signed URL | bearer | cookie
POST,GET,DELETE /mcp                                         bearer (401 + WWW-Authenticate w/ scope)
```

Tool-result shape for frames (`get_frames(return="url")`):

```json
{"frame_id":"a1b2","video_id":"dQw4","t":123.4,
 "url":"https://vidtheque.example.com/frames/a1b2.jpg?exp=1785000000&sig=Yk9…"}
```

Config sanity checks at boot (these are the failures that eat an afternoon):
`PUBLIC_URL` set and HTTPS when `AUTH=oauth`; its host in `TransportSecuritySettings.allowed_hosts`
(else 421 on every request); PRM `resource` string-identical to `PUBLIC_URL + /mcp`.

---

## 7. Open questions for Tom

1. **Self-contained issuer as the default — confirm?** It is the whole reason this is
   self-hostable (no Auth0 for a person running a Pi), but both frameworks' docs shout "don't write
   your own OAuth server". My read: the warning targets multi-tenant SaaS with a real user
   directory. Ours is single-owner, one password, no password reset, no user management, HS256
   local tokens — a materially smaller surface. Do you want a **second, optional** mode
   `AUTH=jwt` that just verifies a JWKS from an existing Authentik/Keycloak/Pocket ID for people
   who already run one? (~30 lines, `JWTVerifier`-shaped.)

2. **Single-user or multi-user?** Everything above assumes **one owner**: `sub` is constant, tools
   don't filter by user, no user table. Multi-user would mean per-user corpora or ACLs, a user
   table, and per-user consent — a different product. Also decides whether `auth.db` is separate
   from the corpus DB (I lean separate, so a corpus rebuild/copy never touches credentials).

3. **Owner credential UX.** `VIDTHEQUE_PASSWORD` env var (plaintext in the compose file) vs. a
   pairing code printed to the container log on first boot and exchanged for a long-lived
   browser cookie? The second is nicer and secret-free but harder to document.

4. **CIMD-only or CIMD + DCR?** I recommend both (Claude prefers CIMD when advertised; Cursor and
   others may still need DCR). DCR costs us a table and a `/register` route. Dropping DCR is
   cleaner and matches where the spec is going, but is a compatibility bet.

5. **Frame URL TTL.** Signed URLs are capability URLs — anyone with the link gets the JPEG until it
   expires. Short TTL (15 min) means links in a saved conversation go dead when the user scrolls
   back; long TTL (7 days) means a leaked transcript leaks frames. Options: short TTL + a
   `/frames/…` 401 page that offers "sign in to view", or make TTL configurable with a documented
   default. Also: should mode `none` sign at all (I say no)?

6. **Framework bet.** Committing to the official SDK now means no FastMCP middleware/transforms and
   a possible migration later if the ecosystem consolidates on FastMCP 4. Alternative: pin
   `fastmcp==4.0.0b2` today and accept beta churn for CIMD-for-free plus its storage layer. Happy
   to go either way — but the choice should be made once, now, not drifted into.

7. **Scopes.** Is `vidtheque:read` / `vidtheque:write` the right split (write = `index_video`,
   subscriptions, deletes), or is a single scope enough for a single-owner server? Two scopes buys
   us the 403 step-up flow for indexing, which is a nice demo but is UX friction on a personal tool.

8. **Public URL / deployment contract.** The PRM `resource` must byte-match what the user types
   into Claude. Do we require an explicit `PUBLIC_URL` (my recommendation — fail fast at boot), or
   try to infer it from `Host`/`X-Forwarded-*` behind the cloudflared tunnel (convenient, and a
   spoofing surface)?

---

## Sources

- MCP 2026-07-28 authorization spec — https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization
- MCP 2026-07-28 release post — https://blog.modelcontextprotocol.io/posts/2026-07-28/
- MCP Python SDK v2 what's new — https://py.sdk.modelcontextprotocol.io/whats-new/
- MCP Python SDK authorization — https://py.sdk.modelcontextprotocol.io/run/authorization/
- MCP Python SDK ASGI / mounting — https://py.sdk.modelcontextprotocol.io/run/asgi/
- SDK releases — https://github.com/modelcontextprotocol/python-sdk/releases
- SDK auth internals — `src/mcp/server/auth/{routes,settings,provider}.py`, `src/mcp/shared/auth.py`
- SDK AS/RS example — https://github.com/modelcontextprotocol/python-sdk/tree/main/examples/servers/simple-auth
- Claude connector authentication — https://claude.com/docs/connectors/building/authentication
- Claude lazy authentication (401 shape, CIMD example) — https://claude.com/docs/connectors/building/lazy-authentication
- Claude custom remote MCP connectors / request headers — https://claude.com/docs/connectors/custom/remote-mcp
- Claude Code MCP — https://code.claude.com/docs/en/mcp
- FastMCP auth overview — https://gofastmcp.com/servers/auth/authentication
- FastMCP full OAuth server — https://gofastmcp.com/servers/auth/full-oauth-server
- FastMCP HTTP deployment — https://github.com/jlowin/fastmcp/blob/main/docs/deployment/http.mdx
- FastMCP 4 what's new — https://github.com/jlowin/fastmcp/blob/main/docs/getting-started/whats-new.mdx
- FastMCP source: `fastmcp_slim/fastmcp/server/auth/{auth,cimd,jwt_issuer}.py`, `oauth_proxy/proxy.py`, `providers/in_memory.py`
- FastMCP 2.13 storage/security — https://jlowin.dev/blog/fastmcp-2-13
- py-key-value — https://strawgate.com/py-key-value/
- claude-code#31208 (ImageContent as text, closed not-planned) — https://github.com/anthropics/claude-code/issues/31208
- claude-ai-mcp#238 / anthropic-sdk-python#1329 / claude-code#53256 (images not rendered inline)
- claude-ai-mcp#402 (no authless option in org connector UI, closed not-planned) — https://github.com/anthropics/claude-ai-mcp/issues/402
- copilot-cli#1305 (CIMD support request) — https://github.com/github/copilot-cli/issues/1305
- Cursor MCP auth — https://www.truefoundry.com/blog/mcp-authentication-in-cursor-oauth-api-keys-and-secure-configuration
