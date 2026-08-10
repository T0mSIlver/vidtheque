"""Token discipline: truncation, clamps, deep links, TSV, pagination lines.

Everything here is the *second* cap. Item counts are capped per tool, character
budgets are capped here, and the response as a whole is capped last
(tool-surface §3.3).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Iterable, Sequence

TRUNCATION_MARKER = "…[{n} chars truncated — pass max_text_chars=0 for full text]…"


def clamp(value: int | None, low: int, high: int, default: int) -> int:
    """Server-side clamp. Never a prompt-only limit — screenpipe's live bug."""
    if value is None:
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def clamp_text_chars(value: int | None, low: int, high: int, default: int) -> int:
    """`max_text_chars`-shaped clamp: 0 opts out, 1..low-1 clamps *up* to low.

    A truncation window smaller than the marker is useless, so it is raised
    rather than honoured. 0 is the tested opt-out — screenpipe once shipped a
    build where 0 returned only the marker.
    """
    if value is None:
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    if number == 0:
        return 0
    if number < low:
        return low
    return min(number, high)


def middle_truncate(text: str, max_chars: int) -> str:
    """Middle-truncate: both ends of a transcript sentence carry signal."""
    if max_chars == 0 or len(text) <= max_chars:
        return text
    # Reserve room for the marker so the result never exceeds the budget by
    # more than the marker itself, which is what the marker is for.
    dropped = len(text) - max_chars
    marker = TRUNCATION_MARKER.format(n=dropped)
    keep = max_chars
    head = keep // 2
    tail = keep - head
    return text[:head] + marker + (text[-tail:] if tail else "")


def clock(seconds: float | None) -> str:
    """`1:12:03` for hours, `12:03` below an hour. Matches the payload examples."""
    if seconds is None:
        return "?"
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def duration_clock(seconds: float | None) -> str:
    """Always `h:mm:ss`, for the duration column in list-videos."""
    if seconds is None:
        return "?"
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


def deeplink_t(t: float | None, lead_s: int = 2) -> int:
    """The `?t=` seconds for an item at `t`: clamped floor of `t` minus the lead.

    The one implementation of §3.6's arithmetic, so a payload that prints the
    compact `?t=<int>` form cannot disagree with one that prints the whole URL.
    `video-summary` built its three `?t=` columns by hand and shipped the bare
    floor, which is 2 s later than every other tool and than the rule the guide
    teaches — an agent that had been told never to invent a timestamp invented
    one to correct for it (terra eval §4.3).
    """
    if t is None:
        return 0
    return max(0, int(t) - lead_s)


def deeplink(video_id: str | None, t: float | None, lead_s: int = 2) -> str | None:
    """`https://youtu.be/<id>?t=<int>`, clamped floor of start minus the lead.

    Always present as a field (None for non-YouTube sources) so the model's
    rendering does not branch.
    """
    if video_id is None or t is None:
        return None
    if ":" in video_id:  # `<source>:<id>` — not a YouTube id
        return None
    return f"https://youtu.be/{video_id}?t={deeplink_t(t, lead_s)}"


def iso_day(ts: int | None) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d")


def iso_minute(ts: int | None) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d %H:%M")


def iso_z(ts: int | float | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(int(ts), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def last_page_offset(total: int, limit: int) -> int:
    """The offset the last page starts at, for a total of `total` rows.

    One definition, because two tools print it: `search`'s past-the-end payload
    and `pagination_line`'s. `0` for an empty result set — there is no page to
    go back to, and `offset=0` is where the caller should start again.
    """
    return max(0, ((total - 1) // max(1, limit)) * limit)


def pagination_line(
    noun: str,
    shown: int,
    offset: int,
    limit: int,
    has_more: bool,
    probe_total: int,
    probe_hit_ceiling: bool,
) -> str:
    """tool-surface §3.4 rendering. Never an exact total from a second query.

    May return two lines: past the last page, the second one says where the end
    is (§3.4 rule 4), which is what `search` has always done and `list-videos`
    did not.
    """
    if not has_more:
        if shown == 0 and offset > 0:
            # Past the last page. `total = offset + shown` collapses onto the
            # offset here — `Videos: 0/200` beside `approx_total: 181` in the
            # same payload (terra eval §9.1) — and it is exactly the "the total
            # moves with the page you asked for" shape §3.4 removed from the
            # in-range case. The probe is exact on this path: an empty page
            # means `total <= offset < ceiling`, so the probe never hit its
            # ceiling and `probe_total` is the real count.
            last = last_page_offset(probe_total, limit)
            unit = noun.lower() if probe_total != 1 else noun.lower().rstrip("s")
            return (
                f"{noun}: 0/{probe_total} (past the last page)\n"
                f"This call has {probe_total} {unit}; the last page starts at "
                f"offset={last}. next: re-run with offset={last}, or offset=0 for "
                "the top."
            )
        total = offset + shown
        return f"{noun}: {shown}/{total} (no more results)"
    if probe_hit_ceiling:
        rounded = (probe_total // 10) * 10
        return f"{noun}: {shown}/~{rounded}+ (use offset={offset + limit} for more)"
    return f"{noun}: {shown}/{probe_total} (use offset={offset + limit} for more)"


def tsv(rows: Iterable[dict[str, Any]], fields: Sequence[str]) -> str:
    """Columnar TSV: writing the keys once is the win (−73% vs JSON)."""
    out = ["\t".join(fields)]
    for row in rows:
        out.append("\t".join(_tsv_cell(row.get(f)) for f in fields))
    return "\n".join(out)


def _tsv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    text = str(value)
    return text.replace("\t", " ").replace("\n", " ").replace("\r", " ")


def cap_response(blocks: Sequence[str], max_chars: int, noun: str = "results") -> tuple[str, int]:
    """Join item blocks, dropping from the tail if the response cap binds.

    Returns the joined body and the number of blocks dropped.
    """
    kept: list[str] = []
    used = 0
    dropped = 0
    for index, block in enumerate(blocks):
        cost = len(block) + 1
        if used + cost > max_chars and kept:
            dropped = len(blocks) - index
            break
        kept.append(block)
        used += cost
    body = "\n".join(kept)
    if dropped:
        body += (
            f"\nResponse truncated at {max_chars} chars — {dropped} {noun} dropped. "
            "Re-run with a smaller limit or max_text_chars."
        )
    return body, dropped


_TAG_RE = re.compile(r"^[a-z0-9]+:[a-z0-9][a-z0-9._-]{0,63}$")
VALID_NAMESPACES = ("topic", "person", "project", "source", "lang", "series")


def validate_tag(tag: str) -> tuple[str, str]:
    """Validate `<ns>:<name>` per tool-surface §3.7.

    The schema `CHECK` constraints re-implement this; the two copies have
    different jobs — this one produces a *helpful typed error*, the schema copy
    is the guarantee no code path can create `topic:x` / `Topic:X` triplicates.
    """
    from .errors import bad_param

    candidate = tag.strip()
    if not _TAG_RE.match(candidate):
        raise bad_param(
            f'Invalid tag "{tag}".',
            "tags are <namespace>:<value>, lowercase, matching "
            "^[a-z0-9]+:[a-z0-9][a-z0-9._-]{0,63}$ — e.g. topic:attention.",
        )
    ns, name = candidate.split(":", 1)
    if ns not in VALID_NAMESPACES:
        raise bad_param(
            f'Unknown tag namespace "{ns}".',
            f"valid namespaces are: {', '.join(VALID_NAMESPACES)}.",
        )
    return ns, name


def split_csv(value: str | None, limit: int, param: str) -> list[str]:
    from .errors import bad_param

    if not value:
        return []
    items = [item.strip() for item in value.split(",") if item.strip()]
    if len(items) > limit:
        raise bad_param(
            f"{param} accepts at most {limit} values, got {len(items)}.",
            f"pass fewer than {limit} values.",
        )
    return items
