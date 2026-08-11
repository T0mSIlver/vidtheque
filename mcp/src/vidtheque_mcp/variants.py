"""Which frame variants exist — the finite vocabulary, and nothing else.

A module of its own because two packages need it and neither may import the
other: ``http/derived.py`` encodes and caches variants, ``auth/tokens.py``
signs URLs for them, and `http/__init__` imports `frames`, which imports
`auth.login`. Anything shared between them has to sit outside both.

**Why the vocabulary is finite.** ``variant_key`` includes the width *and* the
quality, so the old range clamps (64..1280, 20..95) admitted 1,217 x 76 =
92,492 distinct cache entries per frame — roughly 320 million across a 3,460
keyframe corpus, against a 256 MiB LRU. Every miss is a full decode plus a
re-encode with ``optimize=True`` on the request path, so a crawler varying `w`
by one could hold the cache permanently cold and the CPU permanently busy, at
120 requests a minute, indefinitely. The clamps bounded the *values* and never
the *key space*. (2026-08-10 audit, F-5.)

Requests snap to the nearest member rather than being rejected, so no URL
anywhere breaks: an odd width still returns an image, just a neighbouring one.
Both the signer and the route snap, so the two always agree about what a signed
URL means.
"""

from __future__ import annotations

# Every width the product actually asks for: the demo's list thumb and lightbox
# (320, 960), the dashboard's strip, detail and lightbox (192, 512, 1280), and
# the default a bare `/frames/<id>.jpg` resolves to (512).
ALLOWED_WIDTHS = (192, 320, 512, 960, 1280)

# `THUMB_QUALITY` is 70 and the bare-URL default is 75. Two is the whole set:
# quality is the axis nothing varies on purpose, and it multiplied the key
# space by 76.
ALLOWED_QUALITIES = (70, 75)


def snap(value: int, allowed: tuple[int, ...]) -> int:
    """The nearest allowed value, ties going to the smaller one."""
    return min(allowed, key=lambda candidate: (abs(candidate - value), candidate))
