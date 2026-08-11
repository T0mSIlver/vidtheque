"""The `/frames/<id>.jpg` route: `w`/`q` applied, and the `derived/` cache.

The route used to accept `w` and `q`, bind them into the URL signature, and then
serve the stored keyframe — a demo results page shipping 886 KB of
full-resolution JPEG for ten 96x54 thumbnails. These tests are the contract for
the fix: parameters are applied, clamped, never used to upscale, cached in
bytes, encoded once per key, and the signature still covers exactly what it
covered before.

Everything here is CPU-only and generates its own tiny JPEGs.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from starlette.testclient import TestClient

from vidtheque_mcp.app import build_app
from vidtheque_mcp.auth.tokens import FrameUrlSigner, TokenIssuer
from vidtheque_mcp.config import Settings
from vidtheque_mcp.http import derived as derived_module
from vidtheque_mcp.http.derived import DerivedCache, encode_variant, variant_key

from .conftest import seed

FRAME_ID = "kCc8FmEb1nY-00000"
VIDEO_ID = "kCc8FmEb1nY"
SRC_W, SRC_H = 640, 360


def make_jpeg(width: int = SRC_W, height: int = SRC_H, tint: int = 0, quality: int = 92) -> bytes:
    """A real JPEG with real detail — flat colour would compress to nothing."""
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    assert pixels is not None
    for y in range(height):
        for x in range(width):
            pixels[x, y] = ((x * 7 + tint) % 256, (y * 13 + tint) % 256, (x ^ y) % 256)
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


SOURCE = make_jpeg()


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
    """The seeded corpus, with its placeholder JPEGs replaced by decodable ones."""
    data = tmp_path / "data"
    (data / "keyframes").mkdir(parents=True)
    seed(data / "vidtheque.db", data / "keyframes")
    for path in (data / "keyframes").glob("*/*.jpg"):
        path.write_bytes(SOURCE)
    return data


@pytest.fixture
def undecodable(tmp_path: Path) -> Path:
    """The same corpus with conftest's fake JPEGs left in place."""
    data = tmp_path / "fake"
    (data / "keyframes").mkdir(parents=True)
    seed(data / "vidtheque.db", data / "keyframes")
    return data


def client(settings: Settings) -> TestClient:
    from .conftest import FakeEmbeddings

    return TestClient(
        build_app(settings, embeddings=FakeEmbeddings(), run_pipeline=False),
        base_url=settings.public_url,
    )


def size_of(payload: bytes) -> tuple[int, int]:
    with Image.open(BytesIO(payload)) as image:
        return image.size


def variants(data_dir: Path) -> list[Path]:
    return sorted((data_dir / "derived").glob("*/*.jpg"))


# ------------------------------------------------------------------ the route


def test_no_params_is_a_byte_identical_passthrough(corpus: Path) -> None:
    with client(make_settings(corpus)) as c:
        response = c.get(f"/frames/{FRAME_ID}.jpg")
    assert response.status_code == 200
    assert response.content == SOURCE
    assert response.headers["content-type"] == "image/jpeg"
    assert int(response.headers["content-length"]) == len(SOURCE)
    # Nothing was derived, so nothing was written.
    assert not (corpus / "derived").exists()


def test_width_resizes_and_preserves_aspect(corpus: Path) -> None:
    with client(make_settings(corpus)) as c:
        response = c.get(f"/frames/{FRAME_ID}.jpg?w=192")
    assert response.status_code == 200
    assert size_of(response.content) == (192, 108)
    assert int(response.headers["content-length"]) == len(response.content)
    # The whole point: a thumbnail is a fraction of the frame it came from.
    assert len(response.content) < len(SOURCE) / 4


def test_an_arbitrary_width_snaps_to_the_nearest_the_product_uses(corpus: Path) -> None:
    """The clamps bounded the values; they never bounded the *key space*.

    `variant_key` carries both numbers, so 1,217 widths x 76 qualities was
    92,492 cache entries per frame against a 256 MiB LRU, each miss a decode
    plus a re-encode on the request path. Snapping rather than rejecting means
    an odd width still returns an image — a neighbouring one.
    (2026-08-10 audit, F-5.)
    """
    with client(make_settings(corpus)) as c:
        # Below the floor, above nothing: 1 and -500 both land on the smallest
        # width the product actually asks for.
        assert size_of(c.get(f"/frames/{FRAME_ID}.jpg?w=1").content) == (192, 108)
        assert size_of(c.get(f"/frames/{FRAME_ID}.jpg?w=-500").content) == (192, 108)
        # And a plausible-looking width in between snaps to its neighbour.
        assert size_of(c.get(f"/frames/{FRAME_ID}.jpg?w=300").content) == (320, 180)
        assert size_of(c.get(f"/frames/{FRAME_ID}.jpg?w=500").content) == (512, 288)
    # Three requests either side of 320 are one cache entry, not three.
    assert {p.name for p in variants(corpus)} == {
        "00000-w192-q75.jpg",
        "00000-w320-q75.jpg",
        "00000-w512-q75.jpg",
    }


def test_width_clamps_to_the_ceiling_and_never_upscales(corpus: Path) -> None:
    with client(make_settings(corpus)) as c:
        response = c.get(f"/frames/{FRAME_ID}.jpg?w=99999")
    # Clamped to 1280, which is wider than the stored frame: the caller gets the
    # original rather than a blown-up copy, and nothing is cached.
    assert response.content == SOURCE
    assert not variants(corpus)


def test_quality_alone_re_encodes_at_the_original_size(corpus: Path) -> None:
    with client(make_settings(corpus)) as c:
        low = c.get(f"/frames/{FRAME_ID}.jpg?q=20")
        high = c.get(f"/frames/{FRAME_ID}.jpg?q=95")
    assert size_of(low.content) == (SRC_W, SRC_H)
    assert size_of(high.content) == (SRC_W, SRC_H)
    assert len(low.content) < len(high.content)
    assert len(low.content) < len(SOURCE)


def test_quality_snaps_too(corpus: Path) -> None:
    with client(make_settings(corpus)) as c:
        assert c.get(f"/frames/{FRAME_ID}.jpg?w=192&q=1").status_code == 200
        assert c.get(f"/frames/{FRAME_ID}.jpg?w=192&q=100000").status_code == 200
    keys = {path.name for path in variants(corpus)}
    assert keys == {"00000-w192-q70.jpg", "00000-w192-q75.jpg"}


def test_unparseable_params_fall_back_to_the_signed_defaults(corpus: Path) -> None:
    """Garbage is not an error: it is the default the signature already covers."""
    with client(make_settings(corpus)) as c:
        response = c.get(f"/frames/{FRAME_ID}.jpg?w=wide&q=nice")
    assert size_of(response.content) == (512, 288)
    assert [path.name for path in variants(corpus)] == ["00000-w512-q75.jpg"]


def test_the_variant_lands_where_index_schema_says(corpus: Path) -> None:
    with client(make_settings(corpus)) as c:
        c.get(f"/frames/{FRAME_ID}.jpg?w=192&q=70")
    assert variants(corpus) == [corpus / "derived" / VIDEO_ID / "00000-w192-q70.jpg"]


def test_second_request_is_served_from_derived_without_re_encoding(corpus: Path) -> None:
    with client(make_settings(corpus)) as c:
        first = c.get(f"/frames/{FRAME_ID}.jpg?w=192&q=70")
        [path] = variants(corpus)
        stamp = path.stat().st_mtime_ns
        second = c.get(f"/frames/{FRAME_ID}.jpg?w=192&q=70")
    assert second.content == first.content
    assert variants(corpus) == [path]
    # Every encode ends in a write, so an untouched file is a cache hit.
    assert path.stat().st_mtime_ns == stamp


def test_an_undecodable_frame_serves_the_original(undecodable: Path) -> None:
    """One bad file must not 500 a whole results page."""
    stored = (undecodable / "keyframes" / VIDEO_ID).glob("00000-*.jpg")
    payload = next(stored).read_bytes()
    with client(make_settings(undecodable)) as c:
        response = c.get(f"/frames/{FRAME_ID}.jpg?w=96")
    assert response.status_code == 200
    assert response.content == payload
    assert not variants(undecodable)


# --------------------------------------------------------------- cache headers


def test_open_mode_is_publicly_cacheable(corpus: Path) -> None:
    with client(make_settings(corpus)) as c:
        response = c.get(f"/frames/{FRAME_ID}.jpg?w=96")
    assert response.headers["cache-control"] == "public, max-age=86400"


def test_the_max_age_is_env_tunable(corpus: Path) -> None:
    with client(make_settings(corpus, frame_cache_max_age_s=60)) as c:
        response = c.get(f"/frames/{FRAME_ID}.jpg?w=96")
    assert response.headers["cache-control"] == "public, max-age=60"


def test_a_bearer_response_is_private_and_a_signed_one_is_public(corpus: Path) -> None:
    settings = make_settings(corpus, auth_mode="token", static_token="s3cret")
    signer = FrameUrlSigner(settings.resolve_secret(), settings.frame_url_ttl_s)
    expires_at, signature = signer.sign(FRAME_ID, 96, 60)
    with client(settings) as c:
        signed = c.get(f"/frames/{FRAME_ID}.jpg?w=192&q=70&exp={expires_at}&sig={signature}")
        bearer = c.get(
            f"/frames/{FRAME_ID}.jpg?w=192&q=70", headers={"Authorization": "Bearer s3cret"}
        )
        denied = c.get(f"/frames/{FRAME_ID}.jpg?w=192&q=70")

    # The URL is the capability, so a shared cache holding it changes nothing —
    # but it may not hold it for longer than the signature lives.
    kind, _, max_age = signed.headers["cache-control"].partition(", max-age=")
    assert kind == "public"
    assert 0 < int(max_age) <= settings.frame_url_ttl_s
    # A bearer response is one caller's; a shared cache must not re-serve it.
    assert bearer.headers["cache-control"] == "private, max-age=86400"
    assert denied.status_code == 401
    assert denied.headers["cache-control"] == "no-store"


# ------------------------------------------------------------------ signatures


def test_signed_urls_serve_the_resized_bytes_and_tampering_fails(corpus: Path) -> None:
    settings = make_settings(corpus, auth_mode="token", static_token="s3cret")
    signer = FrameUrlSigner(settings.resolve_secret(), settings.frame_url_ttl_s)
    url, _expires = signer.url(settings.public_url, FRAME_ID, 96, 60)
    query = url.split("?", 1)[1]
    expires_at = query.split("exp=")[1].split("&")[0]
    signature = query.split("sig=")[1]

    with client(settings) as c:
        good = c.get(f"/frames/{FRAME_ID}.jpg?{query}")
        # The parameters are signature-bound. Both sides snap, so a *number*
        # one away is the same variant and therefore the same capability —
        # what the MAC must refuse is being pointed at a different image.
        tampered_w = c.get(f"/frames/{FRAME_ID}.jpg?w=960&q=70&exp={expires_at}&sig={signature}")
        tampered_q = c.get(f"/frames/{FRAME_ID}.jpg?w=192&q=75&exp={expires_at}&sig={signature}")
        dropped = c.get(f"/frames/{FRAME_ID}.jpg?exp={expires_at}&sig={signature}")
        stale_exp, stale_sig = signer.sign(FRAME_ID, 192, 70, now=int(time.time()) - 10**9)
        expired = c.get(f"/frames/{FRAME_ID}.jpg?w=192&q=70&exp={stale_exp}&sig={stale_sig}")

    assert good.status_code == 200
    assert size_of(good.content) == (192, 108)
    assert tampered_w.status_code == 401
    assert tampered_q.status_code == 401
    assert dropped.status_code == 401  # signed for 192/70, presented as 512/75
    assert expired.status_code == 401


def test_an_oauth_bearer_still_gets_a_resized_frame(corpus: Path) -> None:
    settings = make_settings(corpus, auth_mode="oauth", password="pw")
    issuer = TokenIssuer(
        settings.resolve_secret(), settings.issuer_url, settings.resource_url, 3600
    )
    token = issuer.issue("owner", "test-client", ["vidtheque:read"])[0]
    with client(settings) as c:
        response = c.get(
            f"/frames/{FRAME_ID}.jpg?w=128", headers={"Authorization": f"Bearer {token}"}
        )
    assert response.status_code == 200
    assert size_of(response.content) == (192, 108)  # 128 snaps to the nearest


# -------------------------------------------------------------- the cache unit


@pytest.fixture
def source_jpeg(tmp_path: Path) -> Path:
    path = tmp_path / "frame.jpg"
    path.write_bytes(SOURCE)
    return path


def counting(monkeypatch: pytest.MonkeyPatch, delay: float = 0.0) -> list[int]:
    """Wrap the encoder so a test can count how often it actually ran."""
    calls: list[int] = []
    real = derived_module.encode_variant

    def wrapped(source: Path, width: int | None, quality: int | None) -> bytes | None:
        calls.append(1)
        if delay:
            time.sleep(delay)
        return real(source, width, quality)

    monkeypatch.setattr(derived_module, "encode_variant", wrapped)
    return calls


async def test_a_hit_never_re_encodes(
    tmp_path: Path, source_jpeg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = counting(monkeypatch)
    cache = DerivedCache(tmp_path / "derived", 1 << 20)
    first = await cache.variant(VIDEO_ID, 0, source_jpeg, 96, 60)
    second = await cache.variant(VIDEO_ID, 0, source_jpeg, 96, 60)
    assert first is not None and second is not None
    assert second.path == tmp_path / "derived" / VIDEO_ID / "00000-w96-q60.jpg"
    assert len(calls) == 1
    assert (cache.encodes, cache.hits) == (1, 1)


async def test_concurrent_requests_for_one_key_encode_once(
    tmp_path: Path, source_jpeg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The thundering results page: ten thumbnails, one resample."""
    calls = counting(monkeypatch, delay=0.05)
    cache = DerivedCache(tmp_path / "derived", 1 << 20)
    results = await asyncio.gather(
        *(cache.variant(VIDEO_ID, 0, source_jpeg, 96, 60) for _ in range(10))
    )
    assert len(calls) == 1
    assert cache.encodes == 1
    assert cache.hits == 9
    assert all(result is not None for result in results)
    # And the per-key locks are not a leak.
    assert cache._locks == {}


async def test_different_keys_do_not_block_each_other(
    tmp_path: Path, source_jpeg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = counting(monkeypatch)
    cache = DerivedCache(tmp_path / "derived", 1 << 20)
    await asyncio.gather(
        cache.variant(VIDEO_ID, 0, source_jpeg, 96, 60),
        cache.variant(VIDEO_ID, 0, source_jpeg, 128, 60),
        cache.variant(VIDEO_ID, 1, source_jpeg, 96, 60),
    )
    assert len(calls) == 3
    assert sorted(p.name for p in (tmp_path / "derived").glob("*/*.jpg")) == [
        "00000-w128-q60.jpg",
        "00000-w96-q60.jpg",
        "00001-w96-q60.jpg",
    ]


async def test_the_byte_cap_evicts_least_recently_used(
    tmp_path: Path, source_jpeg: Path
) -> None:
    root = tmp_path / "derived"
    probe = DerivedCache(root, 1 << 20)
    one = await probe.variant(VIDEO_ID, 0, source_jpeg, 96, 60)
    assert one is not None and one.data is not None
    unit = len(one.data)

    cache = DerivedCache(tmp_path / "capped", unit * 2 + unit // 2)
    for ordinal in range(5):
        assert await cache.variant(VIDEO_ID, ordinal, source_jpeg, 96, 60) is not None
    live = sorted(p.name for p in (tmp_path / "capped").glob("*/*.jpg"))

    assert cache.evictions >= 2
    assert len(live) <= 3
    assert "00004-w96-q60.jpg" in live  # newest survives
    assert "00000-w96-q60.jpg" not in live  # oldest went first
    total = sum(p.stat().st_size for p in (tmp_path / "capped").glob("*/*.jpg"))
    assert total <= cache.max_bytes


async def test_a_hit_refreshes_recency(tmp_path: Path, source_jpeg: Path) -> None:
    root = tmp_path / "derived"
    probe = DerivedCache(tmp_path / "probe", 1 << 20)
    first = await probe.variant(VIDEO_ID, 0, source_jpeg, 96, 60)
    assert first is not None and first.data is not None
    cache = DerivedCache(root, len(first.data) * 2 + 10)

    await cache.variant(VIDEO_ID, 0, source_jpeg, 96, 60)
    await cache.variant(VIDEO_ID, 1, source_jpeg, 96, 60)
    await cache.variant(VIDEO_ID, 0, source_jpeg, 96, 60)  # touch the older one
    await cache.variant(VIDEO_ID, 2, source_jpeg, 96, 60)

    live = {p.name for p in root.glob("*/*.jpg")}
    assert "00001-w96-q60.jpg" not in live  # the one nobody asked for twice
    assert "00000-w96-q60.jpg" in live


async def test_a_variant_over_the_whole_cap_is_served_but_not_stored(
    tmp_path: Path, source_jpeg: Path
) -> None:
    cache = DerivedCache(tmp_path / "derived", 512)
    result = await cache.variant(VIDEO_ID, 0, source_jpeg, 640, 95)
    assert result is not None
    assert result.data is not None and result.path is None
    assert not list((tmp_path / "derived").glob("*/*.jpg"))


async def test_writes_are_atomic_and_leave_no_scratch(
    tmp_path: Path, source_jpeg: Path
) -> None:
    cache = DerivedCache(tmp_path / "derived", 1 << 20)
    await cache.variant(VIDEO_ID, 0, source_jpeg, 96, 60)
    written = list((tmp_path / "derived" / VIDEO_ID).iterdir())
    assert [p.name for p in written if p.name.startswith(".")] == []
    assert [p.name for p in written] == ["00000-w96-q60.jpg"]


async def test_a_previous_process_cache_is_adopted_and_capped(
    tmp_path: Path, source_jpeg: Path
) -> None:
    """Restarting must not blow past the cap, and must evict oldest first."""
    root = tmp_path / "derived"
    warm = DerivedCache(root, 1 << 20)
    for ordinal in range(3):
        await warm.variant(VIDEO_ID, ordinal, source_jpeg, 96, 60)
    # Explicit mtimes: recency on a cold start comes from the filesystem.
    for ordinal, path in enumerate(sorted(root.glob("*/*.jpg"))):
        os.utime(path, (1_000_000 + ordinal, 1_000_000 + ordinal))
    unit = max(p.stat().st_size for p in root.glob("*/*.jpg"))

    cold = DerivedCache(root, unit * 2)
    assert await cold.variant(VIDEO_ID, 9, source_jpeg, 96, 60) is not None
    live = {p.name for p in root.glob("*/*.jpg")}
    assert "00000-w96-q60.jpg" not in live
    assert "00009-w96-q60.jpg" in live
    assert sum(p.stat().st_size for p in root.glob("*/*.jpg")) <= cold.max_bytes


async def test_a_deleted_derived_directory_is_survivable(
    tmp_path: Path, source_jpeg: Path
) -> None:
    """index-schema §6: `rm -rf derived/` is always safe."""
    root = tmp_path / "derived"
    cache = DerivedCache(root, 1 << 20)
    await cache.variant(VIDEO_ID, 0, source_jpeg, 96, 60)
    shutil.rmtree(root)
    again = await cache.variant(VIDEO_ID, 0, source_jpeg, 96, 60)
    assert again is not None and again.path is not None and again.path.is_file()
    assert cache.encodes == 2


# ---------------------------------------------------------------- the encoder


def test_the_encoder_refuses_to_upscale(source_jpeg: Path) -> None:
    assert encode_variant(source_jpeg, SRC_W * 2, None) is None
    assert encode_variant(source_jpeg, SRC_W, None) is None  # equal is not smaller


def test_quality_without_resize_still_encodes(source_jpeg: Path) -> None:
    payload = encode_variant(source_jpeg, SRC_W * 2, 30)
    assert payload is not None
    with Image.open(BytesIO(payload)) as image:
        assert image.size == (SRC_W, SRC_H)


def test_the_encoder_swallows_a_bad_file(tmp_path: Path) -> None:
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"\xff\xd8\xff\xe0not a jpeg\xff\xd9")
    assert encode_variant(broken, 96, 60) is None


def test_variant_keys_separate_the_no_resize_case() -> None:
    assert variant_key(VIDEO_ID, 7, 96, 60) == f"{VIDEO_ID}/00007-w96-q60.jpg"
    assert variant_key(VIDEO_ID, 7, None, 60) == f"{VIDEO_ID}/00007-worig-q60.jpg"
