-- vidtheque index schema v1 — docs/design/index-schema.md.
--
-- Every table is STRICT. Timestamps are INTEGER unix seconds UTC; positions
-- inside a video are REAL seconds. PRAGMA foreign_keys=ON is set on every
-- connection (it is per-connection, not persisted in the file).
--
-- owner_id is the multi-user-ready column from DECISIONS.md #2: single-user
-- behaviour, constant owner 1, no per-user filtering in v1 queries beyond the
-- column existing. Retrofitting it after 150,000 cues reference the old rows
-- is a migration; adding it now is free.

-- ---------------------------------------------------------------- owners

CREATE TABLE owners (
  id         INTEGER PRIMARY KEY CHECK (id = 1),   -- single row, v1
  label      TEXT    NOT NULL DEFAULT 'owner',
  created_at INTEGER NOT NULL DEFAULT (unixepoch())
) STRICT;

INSERT INTO owners (id, label) VALUES (1, 'owner');

-- ---------------------------------------------------------------- config

CREATE TABLE config (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at INTEGER NOT NULL DEFAULT (unixepoch())
) STRICT;

INSERT INTO config (key, value) VALUES
  ('text_embed.model',        'qwen3-embedding-0.6b'),
  ('text_embed.dim',          '1024'),
  ('text_embed.normalized',   '1'),
  ('text_embed.query_prefix', 'query: '),
  ('frame_embed.model',       'siglip2-so400m-patch16-naflex'),
  ('frame_embed.dim',         '1152'),
  ('frame_embed.storage',     'float32'),
  ('stt.model',               'whisperx-large-v3'),
  ('ocr.model',               'rapidocr-v2'),
  ('diarization.enabled',     '0'),
  ('chunk.target_seconds',    '45'),
  ('chunk.overlap_seconds',   '15'),
  ('pipeline.version',        '1');

-- ---------------------------------------------------------------- videos

CREATE TABLE videos (
  id            INTEGER PRIMARY KEY,
  owner_id      INTEGER NOT NULL DEFAULT 1 REFERENCES owners(id),
  source        TEXT    NOT NULL DEFAULT 'youtube',
  source_id     TEXT    NOT NULL,
  public_id     TEXT    GENERATED ALWAYS AS (
                  CASE WHEN source = 'youtube' THEN source_id
                       ELSE source || ':' || source_id END) VIRTUAL,
  url           TEXT    NOT NULL,
  title         TEXT    NOT NULL,
  title_lc      TEXT    GENERATED ALWAYS AS (lower(title)) VIRTUAL,
  description   TEXT,
  channel_id    TEXT,
  channel_name  TEXT,
  channel_lc    TEXT    GENERATED ALWAYS AS (lower(channel_name)) VIRTUAL,
  published_at  INTEGER,
  duration_s    REAL    NOT NULL DEFAULT 0,
  language      TEXT,
  chapters_json TEXT,
  heatmap_json  TEXT,     -- yt-dlp "most replayed", captured at index time
  thumb_path    TEXT,
  media_path    TEXT,
  audio_path    TEXT,
  index_state   TEXT    NOT NULL DEFAULT 'pending'
                CHECK (index_state IN ('pending','indexing','ready','failed','stale')),
  indexed_at    INTEGER,
  added_at      INTEGER NOT NULL DEFAULT (unixepoch()),
  updated_at    INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (source, source_id)
) STRICT;

CREATE INDEX videos_published  ON videos(published_at DESC);
CREATE INDEX videos_channel_lc ON videos(channel_lc, published_at DESC);
CREATE INDEX videos_public_id  ON videos(public_id);
CREATE INDEX videos_state      ON videos(index_state) WHERE index_state <> 'ready';
CREATE INDEX videos_owner      ON videos(owner_id);

-- ------------------------------------------------- per-stage state, chapters

CREATE TABLE video_stages (
  video_id      INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  stage         TEXT    NOT NULL
                CHECK (stage IN ('fetch','stt','chunk','text_embed',
                                 'keyframe','ocr','frame_embed')),
  state         TEXT    NOT NULL DEFAULT 'pending'
                CHECK (state IN ('pending','running','done','failed','skipped')),
  model_key     TEXT,
  stage_version INTEGER NOT NULL DEFAULT 1,
  started_at    INTEGER,
  finished_at   INTEGER,
  error         TEXT,
  PRIMARY KEY (video_id, stage)
) WITHOUT ROWID, STRICT;

CREATE TABLE chapters (
  id       INTEGER PRIMARY KEY,
  video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  seq      INTEGER NOT NULL,
  start_s  REAL    NOT NULL,
  end_s    REAL    NOT NULL,
  title    TEXT    NOT NULL,
  UNIQUE (video_id, seq)
) STRICT;
CREATE INDEX chapters_lookup ON chapters(video_id, start_s);

CREATE TABLE video_links (
  id       INTEGER PRIMARY KEY,
  video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  t_s      REAL,               -- NULL = not timestamped
  url      TEXT    NOT NULL,
  title    TEXT,
  seq      INTEGER NOT NULL,
  UNIQUE (video_id, seq)
) STRICT;
CREATE INDEX video_links_time ON video_links(video_id, t_s) WHERE t_s IS NOT NULL;

-- ------------------------------------------------------- speakers and cues

CREATE TABLE speakers (
  id           INTEGER PRIMARY KEY,
  label        TEXT NOT NULL UNIQUE,
  display_name TEXT,
  merged_into  INTEGER REFERENCES speakers(id) ON DELETE SET NULL,
  created_at   INTEGER NOT NULL DEFAULT (unixepoch())
) STRICT;

CREATE TABLE cues (
  id          INTEGER PRIMARY KEY,
  video_id    INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  seq         INTEGER NOT NULL,
  start_s     REAL    NOT NULL,
  end_s       REAL    NOT NULL,
  text        TEXT    NOT NULL,
  speaker_id  INTEGER REFERENCES speakers(id) ON DELETE SET NULL,
  origin      TEXT    NOT NULL DEFAULT 'whisperx'
              CHECK (origin IN ('whisperx','yt_manual','yt_auto')),
  avg_logprob REAL,
  words_json  TEXT,
  UNIQUE (video_id, seq)
) STRICT;
CREATE INDEX cues_time    ON cues(video_id, start_s);
CREATE INDEX cues_speaker ON cues(speaker_id) WHERE speaker_id IS NOT NULL;

-- -------------------------------------------------- chunks (embedding unit)

CREATE TABLE chunks (
  id           INTEGER PRIMARY KEY,
  video_id     INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  seq          INTEGER NOT NULL,
  start_s      REAL    NOT NULL,
  end_s        REAL    NOT NULL,
  first_cue_id INTEGER NOT NULL REFERENCES cues(id) ON DELETE CASCADE,
  last_cue_id  INTEGER NOT NULL REFERENCES cues(id) ON DELETE CASCADE,
  text         TEXT    NOT NULL,
  n_chars      INTEGER NOT NULL,
  UNIQUE (video_id, seq)
) STRICT;
CREATE INDEX chunks_time ON chunks(video_id, start_s);
CREATE INDEX chunks_span ON chunks(first_cue_id, last_cue_id);

-- ------------------------------------------------------------- keyframes

CREATE TABLE keyframes (
  id           INTEGER PRIMARY KEY,
  video_id     INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  ord          INTEGER NOT NULL,
  t_s          REAL    NOT NULL,
  shot_id      INTEGER NOT NULL,
  shot_start_s REAL    NOT NULL,
  shot_end_s   REAL    NOT NULL,
  phash        INTEGER NOT NULL,        -- 64-bit dct hash, stored SIGNED
  sharpness    REAL    NOT NULL,
  width        INTEGER NOT NULL,
  height       INTEGER NOT NULL,
  jpeg_path    TEXT    NOT NULL,        -- relative to $VIDTHEQUE_DATA_DIR
  jpeg_bytes   INTEGER NOT NULL,
  dup_of       INTEGER REFERENCES keyframes(id) ON DELETE SET NULL,
  ocr_state    TEXT    NOT NULL DEFAULT 'pending'
               CHECK (ocr_state IN ('pending','done','empty','failed','skipped')),
  UNIQUE (video_id, ord),
  UNIQUE (video_id, t_s)
) STRICT;
CREATE INDEX keyframes_time ON keyframes(video_id, t_s);
CREATE INDEX keyframes_shot ON keyframes(video_id, shot_id);
CREATE INDEX keyframes_live ON keyframes(video_id, t_s) WHERE dup_of IS NULL;

CREATE TABLE ocr_lines (
  id          INTEGER PRIMARY KEY,
  keyframe_id INTEGER NOT NULL REFERENCES keyframes(id) ON DELETE CASCADE,
  video_id    INTEGER NOT NULL REFERENCES videos(id)    ON DELETE CASCADE,
  t_s         REAL    NOT NULL,
  line_no     INTEGER NOT NULL,
  text        TEXT    NOT NULL,
  conf        REAL,
  x0 REAL NOT NULL, y0 REAL NOT NULL, x1 REAL NOT NULL, y1 REAL NOT NULL,
  poly_json   TEXT,
  UNIQUE (keyframe_id, line_no)
) STRICT;
CREATE INDEX ocr_time  ON ocr_lines(video_id, t_s);
CREATE INDEX ocr_frame ON ocr_lines(keyframe_id);

-- ------------------------------------------------- tags and collections

CREATE TABLE tags (
  id         INTEGER PRIMARY KEY,
  owner_id   INTEGER NOT NULL DEFAULT 1 REFERENCES owners(id),
  ns         TEXT NOT NULL
             CHECK (ns IN ('topic','person','project','source','lang','series')),
  name       TEXT NOT NULL
             CHECK (length(name) BETWEEN 1 AND 64
                    AND name GLOB '[a-z0-9]*'
                    AND NOT name GLOB '*[^a-z0-9._-]*'),
  full       TEXT GENERATED ALWAYS AS (ns || ':' || name) VIRTUAL,
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (ns, name)
) STRICT;
CREATE INDEX tags_full ON tags(full);

CREATE TABLE video_tags (
  video_id   INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  tag_id     INTEGER NOT NULL REFERENCES tags(id)   ON DELETE CASCADE,
  origin     TEXT    NOT NULL DEFAULT 'manual'
             CHECK (origin IN ('manual','auto','import')),
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  PRIMARY KEY (video_id, tag_id)
) WITHOUT ROWID, STRICT;
CREATE INDEX video_tags_by_tag ON video_tags(tag_id, video_id);

CREATE TABLE collections (
  id           INTEGER PRIMARY KEY,
  owner_id     INTEGER NOT NULL DEFAULT 1 REFERENCES owners(id),
  slug         TEXT NOT NULL UNIQUE,
  title        TEXT NOT NULL,
  description  TEXT,
  kind         TEXT NOT NULL DEFAULT 'manual'
               CHECK (kind IN ('manual','channel','playlist')),
  source_url   TEXT,
  sync_cron    TEXT,
  last_sync_at INTEGER,
  created_at   INTEGER NOT NULL DEFAULT (unixepoch())
) STRICT;

CREATE TABLE collection_videos (
  collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
  video_id      INTEGER NOT NULL REFERENCES videos(id)      ON DELETE CASCADE,
  position      INTEGER,
  added_at      INTEGER NOT NULL DEFAULT (unixepoch()),
  PRIMARY KEY (collection_id, video_id)
) WITHOUT ROWID, STRICT;
CREATE INDEX collection_videos_by_video ON collection_videos(video_id);

-- ------------------------------------------------------------------ jobs

CREATE TABLE jobs (
  id               INTEGER PRIMARY KEY,
  owner_id         INTEGER NOT NULL DEFAULT 1 REFERENCES owners(id),
  public_id        TEXT    NOT NULL UNIQUE,       -- 'job_' || 12 hex
  kind             TEXT    NOT NULL
                   CHECK (kind IN ('index','reindex','delete','export')),
  state            TEXT    NOT NULL DEFAULT 'queued'
                   CHECK (state IN ('queued','running','done','failed','cancelled')),
  priority         INTEGER NOT NULL DEFAULT 100,  -- lower runs first; 'high' = 50
  args_json        TEXT    NOT NULL DEFAULT '{}',
  n_items          INTEGER NOT NULL DEFAULT 0,
  n_done           INTEGER NOT NULL DEFAULT 0,
  n_failed         INTEGER NOT NULL DEFAULT 0,
  cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0,1)),
  error_code       TEXT,
  error_message    TEXT,
  not_before       INTEGER NOT NULL DEFAULT 0,
  created_at       INTEGER NOT NULL DEFAULT (unixepoch()),
  started_at       INTEGER,
  heartbeat_at     INTEGER,
  finished_at      INTEGER
) STRICT;
CREATE INDEX jobs_claim  ON jobs(priority, id) WHERE state = 'queued';
CREATE INDEX jobs_live   ON jobs(heartbeat_at) WHERE state = 'running';
CREATE INDEX jobs_recent ON jobs(created_at DESC);

CREATE TABLE job_items (
  id            INTEGER PRIMARY KEY,
  job_id        INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  seq           INTEGER NOT NULL,
  source_url    TEXT    NOT NULL,
  video_id      INTEGER REFERENCES videos(id) ON DELETE CASCADE,
  state         TEXT    NOT NULL DEFAULT 'queued'
                CHECK (state IN ('queued','running','done','failed','skipped','cancelled')),
  stage         TEXT    CHECK (stage IN ('fetch','stt','chunk','text_embed',
                                         'keyframe','ocr','frame_embed')),
  stage_pct     REAL    NOT NULL DEFAULT 0.0 CHECK (stage_pct BETWEEN 0.0 AND 1.0),
  attempts      INTEGER NOT NULL DEFAULT 0,
  max_attempts  INTEGER NOT NULL DEFAULT 3,
  error_code    TEXT,
  error_message TEXT,
  started_at    INTEGER,
  finished_at   INTEGER,
  UNIQUE (job_id, seq)
) STRICT;
CREATE INDEX job_items_by_video ON job_items(video_id);
CREATE UNIQUE INDEX job_items_one_inflight ON job_items(video_id)
  WHERE video_id IS NOT NULL AND state IN ('queued','running');

CREATE TABLE job_events (
  id        INTEGER PRIMARY KEY,
  job_id    INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  item_id   INTEGER REFERENCES job_items(id) ON DELETE CASCADE,
  at        INTEGER NOT NULL DEFAULT (unixepoch()),
  level     TEXT    NOT NULL DEFAULT 'info'
            CHECK (level IN ('debug','info','warn','error')),
  stage     TEXT,
  message   TEXT    NOT NULL,
  data_json TEXT
) STRICT;
CREATE INDEX job_events_by_job ON job_events(job_id, id);

-- Progress is a rollup maintained by trigger so it cannot be forgotten.
CREATE TRIGGER job_items_roll AFTER UPDATE OF state ON job_items
WHEN new.state IN ('done','failed','skipped','cancelled')
 AND old.state NOT IN ('done','failed','skipped','cancelled')
BEGIN
  UPDATE jobs SET n_done   = n_done   + (new.state = 'done'),
                  n_failed = n_failed + (new.state = 'failed')
  WHERE id = new.job_id;
END;

-- ------------------------------------------------------------------ FTS5
--
-- External content, not standalone: screenpipe measured the delete path at
-- ~38 s standalone vs ~0.09 s external on a 14-day database (~420x), and
-- external content makes the tokenizer a reversible decision.
--
-- porter for prose, unicode61 + tokenchars '_-./' for screen text: `nvidia-smi`
-- and `torch.compile` survive as single tokens instead of being shredded.

CREATE VIRTUAL TABLE cues_fts USING fts5(
  text,
  content='cues',
  content_rowid='id',
  tokenize="porter unicode61 remove_diacritics 2",
  prefix='2 3'
);

CREATE VIRTUAL TABLE ocr_fts USING fts5(
  text,
  content='ocr_lines',
  content_rowid='id',
  tokenize="unicode61 remove_diacritics 2 tokenchars '_-./'",
  prefix='2 3'
);

CREATE VIRTUAL TABLE videos_fts USING fts5(
  title, description, channel_name,
  content='videos',
  content_rowid='id',
  tokenize="porter unicode61 remove_diacritics 2",
  prefix='2 3'
);

-- The guard is `WHEN new.text <> ''`: an empty document adds nothing to the
-- index but still costs a row, and an unguarded delete of a row that was never
-- indexed corrupts the index's internal counts.
--
-- The 'delete' command must be given the OLD values, exactly: external-content
-- FTS5 re-tokenizes what you hand it to find the postings to remove.

CREATE TRIGGER cues_ai AFTER INSERT ON cues WHEN new.text <> '' BEGIN
  INSERT INTO cues_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TRIGGER cues_ad AFTER DELETE ON cues WHEN old.text <> '' BEGIN
  INSERT INTO cues_fts(cues_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;

-- The UPDATE trigger cannot use the WHEN guard: the two branches need
-- different conditions (old non-empty for the delete, new non-empty for the
-- insert). Hence INSERT … SELECT … WHERE.
CREATE TRIGGER cues_au AFTER UPDATE OF text ON cues BEGIN
  INSERT INTO cues_fts(cues_fts, rowid, text)
    SELECT 'delete', old.id, old.text WHERE old.text <> '';
  INSERT INTO cues_fts(rowid, text)
    SELECT new.id, new.text WHERE new.text <> '';
END;

CREATE TRIGGER ocr_ai AFTER INSERT ON ocr_lines WHEN new.text <> '' BEGIN
  INSERT INTO ocr_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TRIGGER ocr_ad AFTER DELETE ON ocr_lines WHEN old.text <> '' BEGIN
  INSERT INTO ocr_fts(ocr_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;

CREATE TRIGGER ocr_au AFTER UPDATE OF text ON ocr_lines BEGIN
  INSERT INTO ocr_fts(ocr_fts, rowid, text)
    SELECT 'delete', old.id, old.text WHERE old.text <> '';
  INSERT INTO ocr_fts(rowid, text)
    SELECT new.id, new.text WHERE new.text <> '';
END;

CREATE TRIGGER videos_ai AFTER INSERT ON videos WHEN new.title <> '' BEGIN
  INSERT INTO videos_fts(rowid, title, description, channel_name)
    VALUES (new.id, new.title, coalesce(new.description,''), coalesce(new.channel_name,''));
END;

CREATE TRIGGER videos_ad AFTER DELETE ON videos WHEN old.title <> '' BEGIN
  INSERT INTO videos_fts(videos_fts, rowid, title, description, channel_name)
    VALUES ('delete', old.id, old.title, coalesce(old.description,''),
            coalesce(old.channel_name,''));
END;

CREATE TRIGGER videos_au AFTER UPDATE OF title, description, channel_name ON videos BEGIN
  INSERT INTO videos_fts(videos_fts, rowid, title, description, channel_name)
    SELECT 'delete', old.id, old.title, coalesce(old.description,''),
           coalesce(old.channel_name,'')
    WHERE old.title <> '';
  INSERT INTO videos_fts(rowid, title, description, channel_name)
    SELECT new.id, new.title, coalesce(new.description,''),
           coalesce(new.channel_name,'')
    WHERE new.title <> '';
END;

-- --------------------------------------------------------------- vectors
--
-- Plain metadata column, NOT `video_id INTEGER PARTITION KEY` — measured trap
-- (index-schema §3.2): vec0 allocates a full chunk per partition (500
-- partitions x 1024 x 1152 x 4 B ~= 2.4 GB to hold 184 MB of vectors), and `k`
-- is applied per partition, so `k=10 AND video_id IN (1,2)` returns 20 rows.

CREATE VIRTUAL TABLE vec_chunks USING vec0(
  chunk_id  INTEGER PRIMARY KEY,
  video_id  INTEGER,
  start_s   FLOAT,
  embedding FLOAT[1024] distance_metric=cosine,
  chunk_size=256
);

CREATE VIRTUAL TABLE vec_frames USING vec0(
  keyframe_id INTEGER PRIMARY KEY,
  video_id    INTEGER,
  t_s         FLOAT,
  embedding   FLOAT[1152] distance_metric=cosine,
  chunk_size=256
);

-- vec0 virtual tables are not reachable by foreign keys: deleting a video
-- cascades through chunks and keyframes and leaves the vectors behind. These
-- two triggers close it.
CREATE TRIGGER chunks_ad AFTER DELETE ON chunks BEGIN
  DELETE FROM vec_chunks WHERE chunk_id = old.id;
END;

CREATE TRIGGER keyframes_ad AFTER DELETE ON keyframes BEGIN
  DELETE FROM vec_frames WHERE keyframe_id = old.id;
END;
