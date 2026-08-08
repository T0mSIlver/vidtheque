"""Cues -> chunks, the embedding unit (index-schema §1.5).

Cues are 1-3 seconds; embedding one is embedding a fragment. The vector leg runs
over overlapping windows — 45 s target, 15 s overlap, both from the `config`
table so index time and query time cannot disagree about what a chunk is.

Two invariants the query layer depends on:

* A chunk's cue span is **contiguous in id**, which is what makes
  ``cues.id BETWEEN first_cue_id AND last_cue_id`` exact rather than
  approximate. That holds because a video's cues are inserted in one pass, in
  time order, in one transaction (§1.4).
* ``chunks.text`` is the exact string that was embedded. It is 15 MB of
  duplication at 500 videos and it stays, because regenerating it after a
  chunker change would silently diverge from what the vectors mean.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .captions import CueDraft


@dataclass
class ChunkDraft:
    seq: int
    start_s: float
    end_s: float
    first_index: int  # index into the cue list, resolved to ids by the writer
    last_index: int
    text: str

    @property
    def n_chars(self) -> int:
        return len(self.text)


def build_chunks(
    cues: Sequence[CueDraft], target_seconds: float = 45.0, overlap_seconds: float = 15.0
) -> list[ChunkDraft]:
    """Windows of `target_seconds`, each starting `target - overlap` after the last.

    Windows are grown by whole cues: a chunk never ends mid-sentence, because
    the thing that gets embedded should read like something a person said.
    """
    if not cues:
        return []
    target = max(1.0, float(target_seconds))
    stride = max(1.0, target - max(0.0, float(overlap_seconds)))

    chunks: list[ChunkDraft] = []
    start_index = 0
    while start_index < len(cues):
        window_start = cues[start_index].start_s
        end_index = start_index
        while end_index + 1 < len(cues) and cues[end_index + 1].end_s - window_start <= target:
            end_index += 1
        text = " ".join(cues[i].text for i in range(start_index, end_index + 1)).strip()
        if text:
            chunks.append(
                ChunkDraft(
                    seq=len(chunks),
                    start_s=window_start,
                    end_s=cues[end_index].end_s,
                    first_index=start_index,
                    last_index=end_index,
                    text=text,
                )
            )
        if end_index + 1 >= len(cues):
            break
        # Advance by the stride in *time*, then snap to the first cue at or
        # after it — so a long silence does not produce a run of empty windows
        # and a burst of fast cues does not produce a hundred overlapping ones.
        next_start = window_start + stride
        advanced = start_index
        for index in range(start_index + 1, len(cues)):
            if cues[index].start_s >= next_start:
                advanced = index
                break
        else:
            advanced = end_index + 1
        start_index = max(advanced, start_index + 1)
    return chunks
