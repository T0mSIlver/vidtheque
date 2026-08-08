"""The on-disk layout from index-schema §6, in one place.

Every path the pipeline writes is built here, because ``keyframes.jpeg_path`` is
stored per row and is authoritative: the day the corpus needs sharding on the
first two characters of ``source_id``, this is the only file that changes.

Filenames are zero-padded, fixed-width and contain no user text. Titles change;
ids do not. ``<ord:05d>-<t_ms:09d>.jpg`` sorts lexically into time order, so
``ls`` is a filmstrip and a mis-scaled timestamp is visible by eye — which is
exactly the bug screenpipe shipped live (ms-vs-fps ``offset_index``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

AUDIO_EXT = {"opus": "opus", "wav": "wav", "flac": "flac"}


@dataclass(frozen=True)
class Layout:
    """Absolute paths under ``$VIDTHEQUE_DATA_DIR``, plus the relative forms
    stored in the database."""

    data_dir: Path

    # ---------------------------------------------------------------- keyframes

    def keyframes_dir(self, source_id: str) -> Path:
        return self.data_dir / "keyframes" / source_id

    def keyframe_relpath(self, source_id: str, ordinal: int, t_s: float) -> str:
        """The value stored in ``keyframes.jpeg_path`` — relative, always."""
        return f"keyframes/{source_id}/{ordinal:05d}-{int(round(t_s * 1000)):09d}.jpg"

    # -------------------------------------------------------------------- media

    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    def audio_path(self, source_id: str, codec: str) -> Path:
        return self.audio_dir() / f"{source_id}.{AUDIO_EXT.get(codec, codec)}"

    def media_dir(self) -> Path:
        return self.data_dir / "media"

    def media_candidates(self, source_id: str) -> list[Path]:
        """The source video, whatever container yt-dlp settled on."""
        directory = self.media_dir()
        if not directory.exists():
            return []
        return sorted(p for p in directory.glob(f"{source_id}.*") if p.is_file())

    # ---------------------------------------------------------------- scratch

    def tmp_dir(self, job_public_id: str) -> Path:
        return self.data_dir / "tmp" / job_public_id

    # ------------------------------------------------------------------ helpers

    def absolute(self, relative: str) -> Path:
        return self.data_dir / relative

    def ensure(self) -> None:
        for child in ("keyframes", "audio", "media", "tmp"):
            (self.data_dir / child).mkdir(parents=True, exist_ok=True)
