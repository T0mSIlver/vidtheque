"""Three transcript shapes, one cue record.

whisperX verbose_json, YouTube auto-caption `json3` (per-word offsets) and
human-authored subtitles all end up as a list of ``CueDraft``. The invariant
that matters, from index-schema §1.4:

    cues.text == ' '.join(word for word, _, _ in words_json)

FTS5 ``highlight()`` returns a character span in ``cues.text``; a character span
maps to a word index by walking the same list. Break the invariant and every
word-level deep link is silently off by a word or two, with nothing to catch it.
So when we have words, the text is *built from* them rather than stored
alongside them.

The auto-caption gotcha that costs a corpus if you miss it (research §5.4): the
ASR json3 stream is a **rolling window**. It emits append events that repeat the
growing caption line, so concatenating event text indexes every sentence two or
three times. Only events carrying `segs` count, `\\n` segs are dropped, and each
word is rebuilt from ``tStartMs + tOffsetMs``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

# A cue that spans more than this is a caption bug or a transcription that ran
# away; clamp so a single row cannot own a whole video's timeline.
MAX_CUE_SECONDS = 30.0


@dataclass
class CueDraft:
    start_s: float
    end_s: float
    text: str
    words: list[tuple[str, float, float]] = field(default_factory=list)
    avg_logprob: float | None = None

    def words_json(self) -> str | None:
        """``[["word", start, end], …]`` with 2-decimal seconds (index-schema §1.4)."""
        if not self.words:
            return None
        return json.dumps([[w, round(s, 2), round(e, 2)] for w, s, e in self.words])


def _from_words(
    words: Sequence[tuple[str, float, float]], avg_logprob: float | None = None
) -> CueDraft | None:
    if not words:
        return None
    text = " ".join(w for w, _, _ in words)
    return CueDraft(
        start_s=words[0][1],
        end_s=max(w[2] for w in words),
        text=text,
        words=list(words),
        avg_logprob=avg_logprob,
    )


# --------------------------------------------------------------------- whisperX


def cues_from_verbose_json(payload: dict[str, Any]) -> list[CueDraft]:
    """whisperX through the worker's OpenAI-compatible verbose_json response.

    Word timings are the reason whisperX is in the stack at all, so a response
    without them is accepted but noted by the caller — segment timing still
    produces usable deep links, just not word-accurate ones.
    """
    segments = payload.get("segments")
    if not isinstance(segments, list):
        return []
    cues: list[CueDraft] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        logprob = segment.get("avg_logprob")
        avg_logprob = float(logprob) if isinstance(logprob, (int, float)) else None
        raw_start = segment.get("start")
        raw_end = segment.get("end")
        words = _whisper_words(
            segment.get("words"),
            segment_start=float(raw_start) if isinstance(raw_start, (int, float)) else None,
            segment_end=float(raw_end) if isinstance(raw_end, (int, float)) else None,
        )
        if words:
            cue = _from_words(words, avg_logprob)
            if cue is not None:
                cues.append(cue)
            continue
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        start = float(segment.get("start") or 0.0)
        end = float(segment.get("end") or start)
        cues.append(
            CueDraft(start_s=start, end_s=max(end, start), text=text, avg_logprob=avg_logprob)
        )
    return _tidy(cues)


def _whisper_words(
    raw: Any, segment_start: float | None = None, segment_end: float | None = None
) -> list[tuple[str, float, float]]:
    """whisperX explicitly permits nullable word timings; anchor them, do not
    invent a zero.

    The first word of a segment losing its `start` used to be placed at video
    time **0.0** — so a deep link into the last minute of a two-hour lecture
    opened at the beginning, and `_from_words` took that 0.0 as the whole cue's
    start. The segment's own bounds are the right anchor, and unplaced words in
    the middle are interpolated across the gap rather than all inheriting the
    previous word's clock.
    """
    if not isinstance(raw, list):
        return []
    tokens: list[tuple[str, float | None, float | None]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        word = str(item.get("word") or item.get("text") or "").strip()
        if not word:
            continue
        start = item.get("start")
        end = item.get("end")
        tokens.append(
            (
                word,
                float(start) if isinstance(start, (int, float)) else None,
                float(end) if isinstance(end, (int, float)) else None,
            )
        )
    if not tokens:
        return []
    floor = segment_start if segment_start is not None else 0.0
    ceiling = segment_end if segment_end is not None else None
    starts = _interpolate([start for _word, start, _end in tokens], floor, ceiling)
    out: list[tuple[str, float, float]] = []
    for index, (word, _start, end) in enumerate(tokens):
        begin = starts[index]
        if end is None:
            end = starts[index + 1] if index + 1 < len(starts) else max(ceiling or begin, begin)
        out.append((word, begin, max(float(end), begin)))
    return out


def _interpolate(
    values: Sequence[float | None], floor: float, ceiling: float | None
) -> list[float]:
    """Fill the gaps between known timings, evenly, and never go backwards."""
    known = [(i, v) for i, v in enumerate(values) if v is not None]
    if not known:
        span = (ceiling - floor) if ceiling is not None and ceiling > floor else 0.0
        step = span / len(values) if values else 0.0
        return [floor + step * i for i in range(len(values))]
    filled: list[float] = [0.0] * len(values)
    first_index, first_value = known[0]
    # Before the first placed word: spread back to the segment's own start.
    head_floor = min(floor, first_value)
    for i in range(first_index):
        share = (i + 1) / (first_index + 1)
        filled[i] = head_floor + (first_value - head_floor) * share
    filled[first_index] = first_value
    for (left, left_value), (right, right_value) in zip(known, known[1:]):
        filled[right] = right_value
        for i in range(left + 1, right):
            share = (i - left) / (right - left)
            filled[i] = left_value + (right_value - left_value) * share
    last_index, last_value = known[-1]
    tail = ceiling if ceiling is not None and ceiling > last_value else last_value
    for i in range(last_index + 1, len(values)):
        share = (i - last_index) / (len(values) - last_index)
        filled[i] = last_value + (tail - last_value) * share
    # Monotonic, whatever the source claimed.
    for i in range(1, len(filled)):
        filled[i] = max(filled[i], filled[i - 1])
    return filled


# ------------------------------------------------------------------- json3


def cues_from_json3(payload: str | dict[str, Any], *, word_timed: bool = True) -> list[CueDraft]:
    """YouTube's timedtext json3 — the auto track carries per-word offsets."""
    data = json.loads(payload) if isinstance(payload, str) else payload
    events = data.get("events")
    if not isinstance(events, list):
        return []

    # `aAppend` events extend the caption line already on screen — that is what
    # the rolling window *is*. A text-bearing one therefore repeats content a
    # full event also carries, so it is dropped. The fallback is the safety net:
    # a track built entirely out of append events would otherwise parse to
    # nothing, and an empty transcript is worse than a duplicated one.
    cues = _json3_events(events, word_timed=word_timed, drop_appends=True)
    if not cues:
        cues = _json3_events(events, word_timed=word_timed, drop_appends=False)
    return _tidy(cues)


def _json3_events(
    events: list[Any], *, word_timed: bool, drop_appends: bool
) -> list[CueDraft]:
    cues: list[CueDraft] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        segs = event.get("segs")
        # No segs -> a window/format event. This is the rolling-window filter.
        if not isinstance(segs, list) or not segs:
            continue
        if drop_appends and event.get("aAppend"):
            continue
        start_ms = float(event.get("tStartMs") or 0.0)
        duration_ms = event.get("dDurationMs")
        tokens: list[tuple[str, float | None]] = []
        for seg in segs:
            if not isinstance(seg, dict):
                continue
            raw = str(seg.get("utf8") or "")
            token = raw.strip()
            if not token:  # the bare "\n" separators
                continue
            offset = seg.get("tOffsetMs")
            # The first seg of an event carries no tOffsetMs — it starts at the
            # event's own tStartMs. Verified in the live sample (research §5.4),
            # where "large" has no offset and " language" has 239. A *later* seg
            # without one is unknown, not "wherever the previous word was":
            # inheriting produced words with identical starts, which walks a
            # word-level deep link back onto the wrong word.
            tokens.append((token, float(offset) if isinstance(offset, (int, float)) else None))
        if not tokens:
            continue
        end_s = (start_ms + float(duration_ms or 0)) / 1000.0
        if word_timed:
            offsets = _interpolate(
                [None if index and offset is None else (offset or 0.0)
                 for index, (_token, offset) in enumerate(tokens)],
                0.0,
                float(duration_ms or 0),
            )
            words = [
                (token, (start_ms + offsets[index]) / 1000.0, 0.0)
                for index, (token, _offset) in enumerate(tokens)
            ]
            cue = _from_words(_close_word_ends(words, end_s))
            if cue is not None:
                cues.append(cue)
            continue
        start = start_ms / 1000.0
        cues.append(
            CueDraft(
                start_s=start,
                end_s=max(end_s, start),
                text=" ".join(token for token, _offset in tokens),
            )
        )
    return cues


def _close_word_ends(
    words: list[tuple[str, float, float]], cue_end_s: float
) -> list[tuple[str, float, float]]:
    """json3 gives word *starts* only; a word ends where the next one begins."""
    out: list[tuple[str, float, float]] = []
    for index, (word, start, _) in enumerate(words):
        end = words[index + 1][1] if index + 1 < len(words) else max(cue_end_s, start)
        out.append((word, start, max(end, start)))
    return out


# ---------------------------------------------------------------------- vtt/srt

# Both WebVTT timestamp grammars. The spec makes the hours component optional
# (`MM:SS.mmm`), and requiring it silently rejected every cue of an otherwise
# valid track — the parser returned no cues and the caller read that as "this
# video has no transcript".
_VTT_STAMP = r"(?:(\d{1,3}):)?(\d{1,2}):(\d{2})[.,](\d{1,3})"
_VTT_TIME = re.compile(rf"{_VTT_STAMP}\s*-->\s*{_VTT_STAMP}")
_TAG = re.compile(r"<[^>]+>")


def cues_from_vtt(payload: str) -> list[CueDraft]:
    """WebVTT/SRT fallback for hosts that serve nothing better.

    Cue-level only. Inline ``<00:00:01.234>`` karaoke timings that YouTube adds
    to some tracks are stripped rather than parsed: they duplicate each line
    across consecutive cues, and de-duplicating them properly is the json3 path.
    """
    cues: list[CueDraft] = []
    block: list[str] = []
    span: tuple[float, float] | None = None
    for line in payload.splitlines() + [""]:
        match = _VTT_TIME.search(line)
        if match:
            span = (_vtt_seconds(match.groups()[:4]), _vtt_seconds(match.groups()[4:]))
            block = []
            continue
        if line.strip():
            if span is not None:
                block.append(_TAG.sub("", line).strip())
            continue
        if span is not None and block:
            text = " ".join(part for part in block if part)
            if text:
                cues.append(CueDraft(start_s=span[0], end_s=max(span[1], span[0]), text=text))
        span, block = None, []
    return _tidy(cues)


def _vtt_seconds(parts: Sequence[str | None]) -> float:
    hours, minutes, seconds, millis = parts
    return (
        int(hours or 0) * 3600
        + int(minutes or 0) * 60
        + int(seconds or 0)
        + int((millis or "0").ljust(3, "0")) / 1000.0
    )


# ------------------------------------------------------------------- shaping


def _tidy(cues: Iterable[CueDraft]) -> list[CueDraft]:
    """Time order, no zero-length rows, no duplicated consecutive text.

    Consecutive-duplicate collapse is the second half of the rolling-window
    defence: a track that repeats a line verbatim at the same timestamp is the
    caption renderer talking to itself, not the speaker repeating themselves.
    """
    ordered = sorted((c for c in cues if c.text.strip()), key=lambda c: (c.start_s, c.end_s))
    out: list[CueDraft] = []
    for cue in ordered:
        cue.end_s = min(max(cue.end_s, cue.start_s + 0.01), cue.start_s + MAX_CUE_SECONDS)
        if out and out[-1].text == cue.text and abs(out[-1].start_s - cue.start_s) < 0.05:
            continue
        if out and out[-1].end_s > cue.start_s:
            out[-1].end_s = max(cue.start_s, out[-1].start_s + 0.01)
        out.append(cue)
    return out
