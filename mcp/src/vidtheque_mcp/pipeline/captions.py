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
        words = _whisper_words(segment.get("words"))
        logprob = segment.get("avg_logprob")
        avg_logprob = float(logprob) if isinstance(logprob, (int, float)) else None
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


def _whisper_words(raw: Any) -> list[tuple[str, float, float]]:
    if not isinstance(raw, list):
        return []
    out: list[tuple[str, float, float]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        word = str(item.get("word") or item.get("text") or "").strip()
        if not word:
            continue
        start = item.get("start")
        end = item.get("end")
        if not isinstance(start, (int, float)):
            # whisperX drops timings for words its aligner could not place
            # (numerals, foreign tokens). Carry the word, borrow the neighbour's
            # clock, keep the join invariant intact.
            start = out[-1][2] if out else 0.0
        if not isinstance(end, (int, float)):
            end = start
        out.append((word, float(start), float(max(end, start))))
    return out


# ------------------------------------------------------------------- json3


def cues_from_json3(payload: str | dict[str, Any], *, word_timed: bool = True) -> list[CueDraft]:
    """YouTube's timedtext json3 — the auto track carries per-word offsets."""
    data = json.loads(payload) if isinstance(payload, str) else payload
    events = data.get("events")
    if not isinstance(events, list):
        return []

    cues: list[CueDraft] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        segs = event.get("segs")
        # No segs -> a window/format event. This is the rolling-window filter.
        if not isinstance(segs, list) or not segs:
            continue
        start_ms = float(event.get("tStartMs") or 0.0)
        duration_ms = event.get("dDurationMs")
        words: list[tuple[str, float, float]] = []
        plain: list[str] = []
        last_offset = 0.0
        for seg in segs:
            if not isinstance(seg, dict):
                continue
            raw = str(seg.get("utf8") or "")
            token = raw.strip()
            if not token:  # the bare "\n" separators
                continue
            offset = seg.get("tOffsetMs")
            if isinstance(offset, (int, float)):
                last_offset = float(offset)
            # The first seg of an event carries no tOffsetMs — it starts at the
            # event's own tStartMs. Verified in the live sample (research §5.4),
            # where "large" has no offset and " language" has 239.
            if word_timed:
                words.append((token, (start_ms + last_offset) / 1000.0, 0.0))
            plain.append(token)
        if words:
            words = _close_word_ends(words, (start_ms + float(duration_ms or 0)) / 1000.0)
            cue = _from_words(words)
            if cue is not None:
                cues.append(cue)
        elif plain:
            text = " ".join(plain)
            start = start_ms / 1000.0
            end = (start_ms + float(duration_ms or 0)) / 1000.0
            cues.append(CueDraft(start_s=start, end_s=max(end, start), text=text))
    return _tidy(cues)


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

_VTT_TIME = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3})"
)
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


def _vtt_seconds(parts: Sequence[str]) -> float:
    hours, minutes, seconds, millis = parts
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis.ljust(3, "0")) / 1000.0


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
