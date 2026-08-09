-- vidtheque migration 0003 — OCR full-text search moves from lines to frames.
--
-- `ocr_fts` indexed one document per OCR *line*. The OCR leg ANDs its terms
-- (`expand_prefix_fts`), so a multi-term query could only ever match when every
-- term landed on the same line — and a keyframe is ~12 lines (41,138 lines over
-- 3,460 keyframes on the live corpus). A slide titled "Vector databases" with
-- "…for retrieval" in a bullet answered `vector retrieval` with silence: a
-- recall hole in exactly the slide-shaped content OCR is best at, with no
-- `note:` and no way to ask for it differently (task #20).
--
-- The unit of OCR search becomes the FRAME. `ocr_frames` carries one row per
-- keyframe: its lines concatenated in reading order (`line_no`, the order the
-- worker returned them) and joined with ' | ' — literally
-- `group_concat(text, ' | ' ORDER BY line_no)`, the same rendering `get-frames`,
-- `video-summary` and `get-segment-context` already print, materialized so FTS5
-- has a content table to point at. `ocr_lines` is untouched and stays the truth:
-- boxes, confidences, per-line ordering, and every display path still read it.
--
-- Why a materialized table rather than a standalone FTS5 index over the
-- concatenation: §2.1's delete-path measurement (~420x) is the whole reason the
-- other two indexes are external-content, and it applies here more strongly, not
-- less — one row per frame is ~12x fewer 'delete' commands per cascaded video
-- than the line index cost. The duplicated text is paid back by dropping
-- `ocr_fts` (measured 14.0 MB at 200,000 lines): one index over ~17x fewer,
-- longer documents.
--
-- Two behaviours change on purpose, both documented in index-schema §2.5 and
-- tool-surface §4.1:
--   * a phrase that WRAPS across two lines now matches (the separator adds no
--     token, so the last token of one line is adjacent to the first of the
--     next) — wrapped slide titles were unfindable before;
--   * `min_chars`/`max_chars` on the OCR leg now measure the frame's whole
--     on-screen text, not one line of it.
--
-- The backfill reads `ocr_lines`, so an existing index upgrades in place — no
-- keyframe is re-read and no worker call is made. It is one transaction with
-- the DDL (the runner wraps every migration in BEGIN IMMEDIATE), and at the
-- 500-video fixture's 200,000 lines it is a single grouped scan.

CREATE TABLE IF NOT EXISTS ocr_frames (
  keyframe_id INTEGER PRIMARY KEY REFERENCES keyframes(id) ON DELETE CASCADE,
  video_id    INTEGER NOT NULL   REFERENCES videos(id)     ON DELETE CASCADE,
  t_s         REAL    NOT NULL,
  text        TEXT    NOT NULL
) STRICT;

-- Same denormalization as `ocr_lines` (§1.7), for the same reason: every OCR
-- query path filters by video and bounds by time, and the candidate CTE must
-- not have to join `keyframes` to do it.
CREATE INDEX IF NOT EXISTS ocr_frames_time ON ocr_frames(video_id, t_s);

-- Same tokenizer as the line index it replaces (§2.2): `unicode61` because
-- screen text must not be stemmed, `tokenchars '_-./'` because `nvidia-smi` and
-- `torch.compile` are single tokens on a slide.
CREATE VIRTUAL TABLE IF NOT EXISTS ocr_frames_fts USING fts5(
  text,
  content='ocr_frames',
  content_rowid='keyframe_id',
  tokenize="unicode61 remove_diacritics 2 tokenchars '_-./'",
  prefix='2 3'
);

CREATE TRIGGER IF NOT EXISTS ocr_frames_ai AFTER INSERT ON ocr_frames
WHEN new.text <> '' BEGIN
  INSERT INTO ocr_frames_fts(rowid, text) VALUES (new.keyframe_id, new.text);
END;

CREATE TRIGGER IF NOT EXISTS ocr_frames_ad AFTER DELETE ON ocr_frames
WHEN old.text <> '' BEGIN
  INSERT INTO ocr_frames_fts(ocr_frames_fts, rowid, text)
    VALUES ('delete', old.keyframe_id, old.text);
END;

CREATE TRIGGER IF NOT EXISTS ocr_frames_au AFTER UPDATE OF text ON ocr_frames BEGIN
  INSERT INTO ocr_frames_fts(ocr_frames_fts, rowid, text)
    SELECT 'delete', old.keyframe_id, old.text WHERE old.text <> '';
  INSERT INTO ocr_frames_fts(rowid, text)
    SELECT new.keyframe_id, new.text WHERE new.text <> '';
END;

-- The backfill. The triggers exist already, so this populates the index too —
-- no separate 'rebuild'. `video_id` and `t_s` are constant within a keyframe by
-- construction (they are denormalized from it), so the bare columns are exact.
INSERT INTO ocr_frames (keyframe_id, video_id, t_s, text)
SELECT o.keyframe_id, o.video_id, o.t_s,
       group_concat(o.text, ' | ' ORDER BY o.line_no)
FROM ocr_lines o
GROUP BY o.keyframe_id;

-- The line index and its triggers go. Keeping both would mean two indexes over
-- the same text, one of them answering `vector retrieval` with silence — and a
-- second write path to keep honest for a query nobody should issue. Derived
-- tables are rebuilt, never migrated (§1.10): if the frame index ever needs to
-- become the line index again, it is a DROP, a CREATE and a 0.8 s rebuild.
DROP TRIGGER IF EXISTS ocr_ai;
DROP TRIGGER IF EXISTS ocr_ad;
DROP TRIGGER IF EXISTS ocr_au;
DROP TABLE IF EXISTS ocr_fts;
