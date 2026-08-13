"""Auth modes, metadata shape, signed URLs, and the frames route matrix."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from vidtheque_mcp.app import build_app
from vidtheque_mcp.auth.cimd import (
    CIMDError,
    guard_ssrf,
    matches_registered_redirect,
    synthesize,
    validate_redirect_uris,
)
from vidtheque_mcp.auth.metadata import (
    authorization_server_metadata,
    protected_resource_metadata,
)
from vidtheque_mcp.auth.modes import build_auth
from vidtheque_mcp.auth.tokens import FrameUrlSigner, TokenIssuer, hash_refresh_token
from vidtheque_mcp.config import ConfigError, OFFLINE_SCOPE, Settings

from .conftest import rpc, rpc_headers, seed

FRAME_ID = "kCc8FmEb1nY-00000"


def make_settings(data_dir: Path, **overrides) -> Settings:
    base = {
        "data_dir": data_dir,
        "public_url": "http://localhost:8080",
        "worker_url": "http://worker:8081",
        "auth_mode": "none",
        "secret": "test-secret",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    (data / "keyframes").mkdir(parents=True)
    seed(data / "vidtheque.db", data / "keyframes")
    return data


def client(settings: Settings) -> TestClient:
    from .conftest import FakeEmbeddings

    # base_url sets the Host header. The transport's DNS-rebinding guard answers
    # 421 to anything not in `allowed_hosts` — the tunnel gotcha, exercised
    # directly in test_unknown_host_is_421 below.
    return TestClient(
        build_app(settings, embeddings=FakeEmbeddings(), run_pipeline=False),
        base_url=settings.public_url,
    )


# ------------------------------------------------------------------- config


def test_token_mode_requires_a_token(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="VIDTHEQUE_TOKEN"):
        make_settings(tmp_path, auth_mode="token").validate()


def test_oauth_mode_requires_a_password(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="VIDTHEQUE_PASSWORD"):
        make_settings(tmp_path, auth_mode="oauth").validate()


def test_oauth_mode_requires_https_off_loopback(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="https"):
        make_settings(
            tmp_path, auth_mode="oauth", password="pw", public_url="http://vid.example.com"
        ).validate()


def test_public_hostname_must_be_in_the_allowlist(tmp_path: Path) -> None:
    """The tunnel 421 gotcha: the transport rejects an unknown Host header."""
    with pytest.raises(ConfigError, match="VIDTHEQUE_PUBLIC_HOSTNAME"):
        make_settings(
            tmp_path, auth_mode="oauth", password="pw", public_url="https://vid.example.com"
        ).validate()

    ok = make_settings(
        tmp_path,
        auth_mode="oauth",
        password="pw",
        public_url="https://vid.example.com",
        public_hostnames=("vid.example.com",),
    )
    ok.validate()
    assert "vid.example.com" in ok.allowed_hosts
    assert "vid.example.com:*" in ok.allowed_hosts


# ----------------------------------------------------------------- mode: none


def test_none_mode_serves_mcp_without_a_token(corpus: Path) -> None:
    with client(make_settings(corpus)) as c:
        assert c.get("/healthz").json()["auth"] == "none"
        # No PRM route is registered, so Claude probes, 404s, connects anonymously.
        assert c.get("/.well-known/oauth-protected-resource").status_code == 404
        response = c.post(
            "/mcp", json=rpc("tools/list"), headers=rpc_headers("tools/list")
        )
        assert response.status_code == 200


def test_none_mode_serves_frames_unsigned(corpus: Path) -> None:
    with client(make_settings(corpus)) as c:
        response = c.get(f"/frames/{FRAME_ID}.jpg")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        assert response.content.startswith(b"\xff\xd8\xff")


# ---------------------------------------------------------------- mode: token


def test_token_mode_401s_without_the_bearer(corpus: Path) -> None:
    settings = make_settings(corpus, auth_mode="token", static_token="s3cret")
    with client(settings) as c:
        response = c.post(
            "/mcp", json=rpc("tools/list"), headers=rpc_headers("tools/list")
        )
        assert response.status_code == 401
        # A transport-level 401 is what makes a client re-authenticate; a 200
        # with isError:true is passed to the model as text. But the challenge
        # is a PLAIN one: `resource_metadata=` here pointed at OAuth discovery
        # this mode does not host, and a client that honored the pointer died
        # in a DCR 404 instead of asking its human for the token (field
        # report, CT 9002, 2026-08-13). No pointer -> clients fall back to
        # configured credentials.
        assert response.headers["www-authenticate"].startswith("Bearer")
        assert "resource_metadata=" not in response.headers["www-authenticate"]


def test_token_mode_accepts_the_bearer(corpus: Path) -> None:
    settings = make_settings(corpus, auth_mode="token", static_token="s3cret")
    with client(settings) as c:
        response = c.post(
            "/mcp", json=rpc("tools/list"), headers=rpc_headers("tools/list", "s3cret")
        )
        assert response.status_code == 200
        assert len(response.json()["result"]["tools"]) == 9


def test_unknown_host_is_421(corpus: Path) -> None:
    """The tunnel footgun: a Host outside the allowlist is Misdirected Request.

    This is what `VIDTHEQUE_PUBLIC_HOSTNAME` exists to prevent, and the reason
    the boot check refuses an oauth config whose public host is not listed.
    """
    settings = make_settings(corpus, auth_mode="token", static_token="s3cret")
    with client(settings) as c:
        response = c.post(
            "/mcp",
            json=rpc("tools/list"),
            headers={**rpc_headers("tools/list", "s3cret"), "Host": "vid.example.com"},
        )
        assert response.status_code == 421


def test_token_mode_publishes_no_prm(corpus: Path) -> None:
    """Token mode hosts no OAuth, so it advertises none — anywhere.

    It used to publish protected-resource metadata naming an authorization
    server that does not exist in this mode, and the /mcp and /frames 401s
    pointed at it. Both well-known paths must 404 like `none` mode's do, so a
    discovering client concludes "no OAuth here" and falls back to configured
    credentials, rather than walking the pointer into a DCR 404.
    """
    settings = make_settings(corpus, auth_mode="token", static_token="s3cret")
    with client(settings) as c:
        assert c.get("/.well-known/oauth-protected-resource/mcp").status_code == 404
        assert c.get("/.well-known/oauth-protected-resource").status_code == 404
        assert c.get("/.well-known/oauth-authorization-server").status_code == 404
        frame = c.get(f"/frames/{FRAME_ID}.jpg")
        assert frame.status_code == 401
        assert "resource_metadata=" not in frame.headers["www-authenticate"]


# -------------------------------------------------------- frames auth matrix


@pytest.mark.parametrize("mode", ["token", "oauth"])
def test_frames_route_auth_matrix(corpus: Path, mode: str) -> None:
    settings = make_settings(
        corpus,
        auth_mode=mode,
        static_token="s3cret" if mode == "token" else None,
        password="pw" if mode == "oauth" else None,
    )
    signer = FrameUrlSigner(settings.resolve_secret(), settings.frame_url_ttl_s)
    expires_at, signature = signer.sign(FRAME_ID, 512, 75)

    with client(settings) as c:
        # 1. no credential at all
        assert c.get(f"/frames/{FRAME_ID}.jpg").status_code == 401

        # 2. a valid signature — the only thing a browser can present
        signed = c.get(
            f"/frames/{FRAME_ID}.jpg?w=512&q=75&exp={expires_at}&sig={signature}"
        )
        assert signed.status_code == 200
        assert signed.headers["content-type"] == "image/jpeg"

        # 3. a signature bound to different parameters
        assert (
            c.get(f"/frames/{FRAME_ID}.jpg?w=256&q=75&exp={expires_at}&sig={signature}").status_code
            == 401
        )

        # 4. an expired signature
        stale_exp, stale_sig = signer.sign(FRAME_ID, 512, 75, now=int(time.time()) - 10**9)
        assert (
            c.get(f"/frames/{FRAME_ID}.jpg?w=512&q=75&exp={stale_exp}&sig={stale_sig}").status_code
            == 401
        )

        # 5. a bearer token instead
        token = _bearer_for(settings)
        assert (
            c.get(
                f"/frames/{FRAME_ID}.jpg", headers={"Authorization": f"Bearer {token}"}
            ).status_code
            == 200
        )

        # 6. a valid credential but an unknown frame
        assert (
            c.get(
                "/frames/kCc8FmEb1nY-09999.jpg", headers={"Authorization": f"Bearer {token}"}
            ).status_code
            == 404
        )


def _bearer_for(settings: Settings) -> str:
    if settings.auth_mode == "token":
        assert settings.static_token
        return settings.static_token
    issuer = TokenIssuer(
        settings.resolve_secret(), settings.issuer_url, settings.resource_url, 3600
    )
    return issuer.issue("owner", "test-client", ["vidtheque:read"])[0]


def test_signed_url_round_trip(tmp_path: Path) -> None:
    signer = FrameUrlSigner("a-secret", ttl_s=3600)
    url, expires_at = signer.url("https://vid.example.com", FRAME_ID, 512, 75)
    assert url.startswith(f"https://vid.example.com/frames/{FRAME_ID}.jpg?w=512&q=75&exp=")
    signature = url.split("sig=")[1]
    assert signer.verify(FRAME_ID, 512, 75, expires_at, signature)
    assert not signer.verify(FRAME_ID, 512, 75, expires_at, signature + "x")
    assert not signer.verify("other-00001", 512, 75, expires_at, signature)
    # expiry
    assert not signer.verify(FRAME_ID, 512, 75, int(time.time()) - 1, signature)


def test_frame_signing_key_is_not_the_access_token_key() -> None:
    """A leaked image link must not be an access token."""
    issuer = TokenIssuer("shared", "https://x", "https://x/mcp")
    token, _ = issuer.issue("owner", "c", ["vidtheque:read"])
    signer = FrameUrlSigner("shared")
    _, signature = signer.sign(FRAME_ID, 512, 75)
    assert signature not in token


# ----------------------------------------------------------- oauth metadata


def test_as_metadata_advertises_cimd(tmp_path: Path) -> None:
    """Claude picks CIMD only when BOTH flags are right."""
    settings = make_settings(
        tmp_path,
        auth_mode="oauth",
        password="pw",
        public_url="https://vid.example.com",
        public_hostnames=("vid.example.com",),
    )
    meta = authorization_server_metadata(settings)
    assert meta["client_id_metadata_document_supported"] is True
    assert "none" in meta["token_endpoint_auth_methods_supported"]
    assert meta["code_challenge_methods_supported"] == ["S256"]
    assert meta["authorization_response_iss_parameter_supported"] is True
    # DCR retained for clients that do not do CIMD yet.
    assert meta["registration_endpoint"].endswith("/register")


def test_offline_access_is_in_the_as_metadata_only(tmp_path: Path) -> None:
    """Claude appends offline_access only if the AS lists it; the spec says the
    protected resource SHOULD NOT. Get it wrong and there is no refresh token."""
    settings = make_settings(
        tmp_path,
        auth_mode="oauth",
        password="pw",
        public_url="https://vid.example.com",
        public_hostnames=("vid.example.com",),
    )
    assert OFFLINE_SCOPE in authorization_server_metadata(settings)["scopes_supported"]
    assert OFFLINE_SCOPE not in protected_resource_metadata(settings)["scopes_supported"]


def test_oauth_metadata_endpoints_are_served(corpus: Path) -> None:
    settings = make_settings(corpus, auth_mode="oauth", password="pw")
    with client(settings) as c:
        meta = c.get("/.well-known/oauth-authorization-server")
        assert meta.status_code == 200
        body = meta.json()
        assert body["client_id_metadata_document_supported"] is True
        assert body["token_endpoint_auth_methods_supported"] == ["none"]
        # Both PRM paths: Claude probes the suffixed one first.
        for path in (
            "/.well-known/oauth-protected-resource/mcp",
            "/.well-known/oauth-protected-resource",
        ):
            prm = c.get(path)
            assert prm.status_code == 200
            assert prm.json()["authorization_servers"] == ["http://localhost:8080"]


def test_our_metadata_wins_over_the_sdk_route(corpus: Path) -> None:
    """The SDK's build_metadata omits both CIMD fields, so its route is dropped."""
    settings = make_settings(corpus, auth_mode="oauth", password="pw")
    bundle = build_auth(settings)
    try:
        paths = [r.path for r in bundle.routes]
        assert paths.count("/.well-known/oauth-authorization-server") == 1
        for expected in ("/authorize", "/token", "/register", "/revoke", "/auth/login"):
            assert expected in paths
    finally:
        bundle.close()


# ------------------------------------------------------------------- CIMD


def test_cimd_document_must_be_self_referential() -> None:
    with pytest.raises(CIMDError, match="self-referential"):
        synthesize(
            "https://claude.ai/oauth/client",
            {"client_id": "https://evil.example/other", "redirect_uris": ["https://claude.ai/cb"]},
        )


def test_cimd_scopeless_documents_get_the_server_scope_policy() -> None:
    """A CIMD document is a self-description, not a grant.

    Claude Code's real document declares no `scope` field — it cannot know
    this server's vocabulary — and the SDK's validate_scope treats a
    scope-less client as allowed-nothing, which refused every CIMD sign-in
    at /authorize with invalid_scope ("Client was not registered with scope
    vidtheque:read"; field report, CT 9002, 2026-08-13). Scope policy is the
    server's: a scope-less document defaults to the set the DCR path
    registers as valid_scopes, a declared scope is kept verbatim, and the
    consent screen stays the place a human grants any of it.
    """
    # The shape of Claude Code's actual metadata document, verbatim fields.
    scopeless = synthesize(
        "https://claude.ai/oauth/claude-code-client-metadata",
        {
            "client_id": "https://claude.ai/oauth/claude-code-client-metadata",
            "client_name": "Claude Code",
            "redirect_uris": ["http://localhost/callback", "http://127.0.0.1/callback"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
    )
    granted = scopeless.validate_scope("vidtheque:read vidtheque:write offline_access")
    assert granted == ["vidtheque:read", "vidtheque:write", "offline_access"]

    declared = synthesize(
        "https://claude.ai/oauth/client",
        {
            "client_id": "https://claude.ai/oauth/client",
            "redirect_uris": ["https://claude.ai/cb"],
            "scope": "vidtheque:read",
        },
    )
    assert declared.validate_scope("vidtheque:read") == ["vidtheque:read"]
    with pytest.raises(Exception, match="not registered with scope"):
        declared.validate_scope("vidtheque:write")


def test_cimd_requires_a_public_auth_method() -> None:
    with pytest.raises(CIMDError, match="token_endpoint_auth_method"):
        synthesize(
            "https://claude.ai/oauth/client",
            {
                "client_id": "https://claude.ai/oauth/client",
                "redirect_uris": ["https://claude.ai/cb"],
                "token_endpoint_auth_method": "client_secret_post",
            },
        )


def test_cimd_redirect_uris_must_be_same_origin() -> None:
    with pytest.raises(CIMDError, match="same-origin"):
        validate_redirect_uris("https://claude.ai/oauth/client", ["https://evil.example/cb"])
    # Loopback is exempt — Claude Code declares localhost and 127.0.0.1.
    validate_redirect_uris(
        "https://claude.ai/oauth/claude-code-client-metadata",
        ["http://localhost/callback", "http://127.0.0.1/callback"],
    )


def test_loopback_redirect_matching_ignores_the_port() -> None:
    """RFC 8252: Claude Code listens on an ephemeral port."""
    registered = ["http://localhost/callback", "http://127.0.0.1/callback"]
    assert matches_registered_redirect(registered, "http://localhost:53821/callback")
    assert matches_registered_redirect(registered, "http://127.0.0.1:1234/callback")
    assert not matches_registered_redirect(registered, "http://localhost:53821/other")
    assert not matches_registered_redirect(registered, "https://evil.example/callback")


def test_cimd_ssrf_guard() -> None:
    for bad in [
        "http://claude.ai/oauth/client",  # not https
        "https://user:pw@claude.ai/c",  # credentials
        "https://claude.ai/c#frag",  # fragment
        "https://127.0.0.1/c",  # loopback
    ]:
        with pytest.raises(CIMDError):
            guard_ssrf(bad)


def test_cimd_clients_are_never_persisted(corpus: Path) -> None:
    settings = make_settings(corpus, auth_mode="oauth", password="pw")
    bundle = build_auth(settings)
    try:
        assert bundle.store is not None
        assert bundle.store.load_client("https://claude.ai/oauth/client") is None
    finally:
        bundle.close()


# --------------------------------------------------------------- JWT tokens


def test_access_tokens_carry_the_audience_and_expire() -> None:
    issuer = TokenIssuer("k", "https://vid.example.com", "https://vid.example.com/mcp", ttl_s=60)
    token, expires_at = issuer.issue("owner", "cli", ["vidtheque:read"])
    claims = issuer.verify(token)
    assert claims is not None
    assert claims["aud"] == "https://vid.example.com/mcp"
    assert claims["sub"] == "owner"
    assert expires_at > int(time.time())

    # A token minted for a different resource is not ours.
    other = TokenIssuer("k", "https://vid.example.com", "https://other.example/mcp")
    assert issuer.verify(other.issue("owner", "cli", ["vidtheque:read"])[0]) is None
    # Nor is one signed with a different key.
    assert issuer.verify(TokenIssuer("other-key", issuer.issuer, issuer.audience).issue(
        "owner", "cli", ["vidtheque:read"]
    )[0]) is None


def test_refresh_tokens_are_stored_by_hash_only() -> None:
    secret = "k"
    raw = "a-refresh-token"
    hashed = hash_refresh_token(secret, raw)
    assert raw not in hashed
    assert hashed == hash_refresh_token(secret, raw)
    assert hashed != hash_refresh_token("other", raw)


def test_loopback_redirects_accept_any_port_at_authorize(corpus: Path) -> None:
    """RFC 8252 §7.3: the port on a loopback redirect is the client's to pick.

    A native client (Claude Code, MCP Inspector) registers the random port it
    held on the day it registered and binds a fresh one at the next sign-in.
    The SDK's handler validates redirect_uri by exact membership BEFORE the
    provider's authorize() — so `matches_registered_redirect`'s loopback rule
    was dead code on the path that needed it, and the second sign-in ever made
    against the box died with "Redirect URI ... not registered for client"
    (field report, CT 9002, 2026-08-13). `LoopbackRedirectClient` puts the
    rule on the model the handler consults. Path and host stay exact; only
    the loopback port floats.
    """
    settings = make_settings(corpus, auth_mode="oauth", password="pw")
    with client(settings) as c:
        registered = c.post(
            "/register",
            json={
                "redirect_uris": ["http://localhost:33418/callback"],
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "client_name": "native-app-under-test",
            },
        )
        assert registered.status_code in (200, 201), registered.text
        client_id = registered.json()["client_id"]

        def authorize(redirect_uri: str):  # type: ignore[no-untyped-def]
            return c.get(
                "/authorize",
                params={
                    "client_id": client_id,
                    "redirect_uri": redirect_uri,
                    "response_type": "code",
                    "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
                    "code_challenge_method": "S256",
                    "state": "s",
                },
                follow_redirects=False,
            )

        # A different loopback port is the RFC's explicitly allowed case: the
        # request proceeds to the login page instead of dying at validation.
        moved = authorize("http://localhost:51965/callback")
        assert moved.status_code in (302, 307), moved.text
        assert "/auth/login" in moved.headers["location"]

        # The rule floats the port and nothing else.
        wrong_path = authorize("http://localhost:51965/elsewhere")
        assert wrong_path.status_code == 400
        assert "not registered" in wrong_path.text
