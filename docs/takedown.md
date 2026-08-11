# Removal — how a creator takes their work out

The positioning contract makes this a public promise, not a courtesy:

> …for that corpus we ask the organisers first, credit them on the page, and
> **take a channel out on request — one row, one command, and we say so
> publicly**. If a creator would rather not be followed, that is a complete
> reason and there is no appeal to make.
>
> — `research/positioning-2026-08-10.md` §9.1, the published answer to "did the
> speakers consent?"

This file is the substance behind that sentence. It is the operator's runbook
and it is deliberately public, because a removal path nobody can read is not a
removal path.

**Scope.** It covers the public demo instance. A self-hosted instance indexes
what its owner chose to watch and the same procedure works, but the promise
above is about the corpus that is *published*.

---

## 1. Asking

You do not have to prove anything, explain why, or be the copyright holder in
any formal sense. If you made the talk, or you ran the event, or you are the
channel, say so and name what should go:

- **a whole channel** — everything indexed from it;
- **one video** — by its URL or its YouTube id;
- **one span inside a video** (a Q&A, a name, a slide that should not have been
  shown) — see §5, which is honest about what is and is not possible here.

**How to ask** — open a **public issue** on
<https://github.com/T0mSIlver/vidtheque/issues> using the *Removal request*
template (decided 2026-08-11). A public issue is deliberate: the removal
becomes visible, which is half of "we say so publicly". If you would rather
not post publicly, say so in a minimal issue ("removal request, details
requested privately") and the operator will take it from there.

Do **not** use the security advisory form in `SECURITY.md` for this — that is
for vulnerabilities and it is private, which is the wrong shape for a takedown.

**Response time** — **acknowledged and actioned within 72 hours** (decided
2026-08-11). This is a one-person project; 72 hours is the honest number. The
removal itself takes minutes (§2), so the promise is about noticing, not
doing.

Until it is answered, a request can always be honoured immediately in its
cheapest form: **stop serving it** (§2.2) while the full delete is scheduled.

---

## 2. Doing it

There is **no delete tool.** `jobs.kind` permits `'delete'` and
`docs/design/index-schema.md` §6.2 designs the job, but nothing implements it —
the runner raises `E_NOT_IMPLEMENTED`, and the dashboard deliberately ships no
delete button rather than a button that queues a failing job
(`mcp/src/vidtheque_mcp/dashboard/writes.py`). So this is a manual procedure,
and it is written out in full so that it is *reliable* rather than clever. See
§7 for the tooling that should exist.

Notation: `$DATA` is `VIDTHEQUE_DATA_DIR`, `$ID` is the YouTube id (the
`videos.public_id`, and the name of every directory on disk).

### 2.0 Two facts about the shell before you open a connection

**You cannot do this with `sqlite3`.** Not "should not" — cannot, and it fails
loudly rather than quietly, which is the one piece of luck in it:

- **`vec_chunks` and `vec_frames` are `vec0` virtual tables**, and the delete
  path goes *through* them: the `chunks_ad` and `keyframes_ad` triggers delete
  the vectors, so any connection without the `sqlite-vec` extension loaded gets
  `OperationalError: no such module: vec0` and the whole `DELETE` rolls back.
  (Verified on a copy of the live database, 2026-08-11.)
- **`PRAGMA foreign_keys` defaults *off* outside the application.** With it off
  every `ON DELETE CASCADE` in the schema silently does nothing and you get an
  orphaned corpus that still answers searches (`index-schema.md` §1: "a
  connection without it silently disables every `ON DELETE CASCADE`").

So open the connection the way the application does — one Python snippet, from
the repo checkout, which loads the extension and sets the pragma:

```python
# removal.py — run with `uv run --no-sync python removal.py`
import sqlite3, sqlite_vec

DB = "/path/to/vidtheque-data/vidtheque.db"      # $DATA/vidtheque.db
conn = sqlite3.connect(DB)
conn.enable_load_extension(True)
sqlite_vec.load(conn)                            # or the vec0 delete fails
conn.enable_load_extension(False)
conn.execute("PRAGMA foreign_keys = ON")         # or the cascade does nothing
```

Every SQL block below runs on that `conn`.

### 2.1 First, find what you are removing

```python
# by channel
for row in conn.execute(
    "SELECT id, public_id, title, index_state FROM videos "
    "WHERE channel_name = ? ORDER BY published_at", ("AI Engineer",)):
    print(row)
# or by video
conn.execute("SELECT id, public_id, title, channel_name FROM videos "
             "WHERE public_id = ?", ("VIDEO_ID",)).fetchall()
```

Write the list down. Everything below is per `public_id`, and the counts you
start with are the counts you check against at the end.

### 2.2 Stop serving it first, if the request is urgent

The delete takes minutes; taking the tunnel down takes seconds and is
completely reversible:

```bash
sudo systemctl stop cloudflared
```

That is the honest interim answer while §2.3–§2.6 are done carefully. It is
also the *only* action that needs no care at all, which is why it is first.

### 2.3 Stop the pipeline

A removal races an indexer. Before deleting anything, confirm nothing is
working on this video and nothing is queued to:

```sql
SELECT state, COUNT(*) FROM jobs      GROUP BY 1;   -- nothing running/queued
SELECT state, COUNT(*) FROM job_items GROUP BY 1;
SELECT index_state, COUNT(*) FROM videos WHERE public_id = 'VIDEO_ID';
```

If a stage is in flight, let it finish or cancel the job terminally — a
half-deleted video that an indexer then re-populates is the worst outcome
available here. (`BEFORE-SHIP.md` 2.1 has the same check for a different
reason.)

### 2.4 The database delete — one row, and the cascade does the rest

```python
conn.execute("BEGIN")
# One video:
conn.execute("DELETE FROM videos WHERE public_id = ?", ("VIDEO_ID",))
# …or a whole channel, in one statement:
# conn.execute("DELETE FROM videos WHERE channel_name = ?", ("CHANNEL NAME",))
conn.execute("COMMIT")
```

That single `DELETE` is the whole database side. What it takes with it:

| removed by | what goes |
|---|---|
| `ON DELETE CASCADE` on `videos(id)` | `video_stages`, `chapters`, `video_links`, `cues`, `chunks`, `keyframes`, `ocr_lines`, `ocr_frames`, `video_tags`, `collection_videos`, and `job_items` rows pointing at it |
| `ON DELETE CASCADE` on `keyframes(id)` | the frame's `ocr_lines` and `ocr_frames`, reached through the keyframe rather than through the video |
| trigger `videos_ad` | the `videos_fts` postings (title, description) |
| trigger `cues_ad` | the `cues_fts` postings — every sentence of the transcript |
| trigger `ocr_frames_ad` | the `ocr_frames_fts` postings — every line that was on screen |
| trigger `chunks_ad` | the `vec_chunks` vectors |
| trigger `keyframes_ad` | the `vec_frames` vectors |

Two facts this table depends on, both verified rather than assumed
(`index-schema.md` §2.3, §3.3): **a cascade delete does fire `AFTER DELETE`
triggers**, which is what keeps the FTS indexes honest without the pipeline
remembering anything; and **`vec0` virtual tables are not reachable by foreign
keys**, which is why the two `*_ad` triggers on `chunks` and `keyframes` exist
at all — without them the vectors survive the video and frame search keeps
returning it.

**Rehearsed on a copy of the live database, 2026-08-11**, deleting one
70-minute conference talk. It is worth reading as a picture of how much of a
corpus one row is:

| table | rows removed |
|---|---:|
| `videos` | 1 |
| `cues` | 279 |
| `chunks` | 33 |
| `keyframes` | 126 |
| `ocr_lines` | 3,772 |
| `ocr_frames` | 122 |
| `vec_chunks` | 33 |
| `vec_frames` | 122 |
| `cues_fts_docsize` | 279 |
| `ocr_frames_fts_docsize` | 122 |
| `videos_fts_docsize` | 1 |

**1.78 s**, one statement, no orphans, all three FTS integrity checks clean.
(`index-schema.md` §6.2 estimated ~1.48 s; the shape is right.) Note the FTS
*docsize* rows moving in lockstep with their content tables — that is the
delete triggers doing their job, and it is the number to look at, because
`SELECT COUNT(*) FROM cues_fts` reads straight through to `cues` and would say
the right thing even if the index had rotted (`index-schema.md` §2.3).

### 2.5 The files — database first, files second

Deliberately in this order: a crash between them leaves orphaned *files*, which
a sweep finds and which answer nothing, rather than rows pointing at files that
are gone.

```bash
rm -rf "$DATA/keyframes/$ID"     # the evidence JPEGs, served at /frames/*
rm -rf "$DATA/derived/$ID"       # the resized-variant LRU cache
rm -f  "$DATA/audio/$ID."*       # the opus the transcript came from
rm -f  "$DATA/media/$ID."*       # usually already gone (KEEP_SOURCE_MEDIA=0)
```

`audio/` is the one worth naming out loud: `index-schema.md` §6.2 keeps it as
"the expensive-to-reproduce input" and it is a full copy of the talk's sound.
A removal that leaves it behind has not removed the recording.

### 2.6 Verify — the corpus, the indexes, the vectors

On the same `conn` from §2.0. Every one of these was run against the rehearsal
and the expected values below are what it actually returned:

```sql
-- 1. It is gone, and the count is what you expect.
SELECT COUNT(*) FROM videos WHERE public_id = 'VIDEO_ID';          -- 0
SELECT COUNT(*) FROM videos;                                       -- N-1

-- 2. Nothing orphaned by the cascade. All four must be 0.
SELECT (SELECT COUNT(*) FROM cues       WHERE video_id NOT IN (SELECT id FROM videos)),
       (SELECT COUNT(*) FROM chunks     WHERE video_id NOT IN (SELECT id FROM videos)),
       (SELECT COUNT(*) FROM keyframes  WHERE video_id NOT IN (SELECT id FROM videos)),
       (SELECT COUNT(*) FROM ocr_lines  WHERE video_id NOT IN (SELECT id FROM videos));

-- 3. No vector outlived its row. THIS is the vector check for a removal, and
--    both must be 0. (Not `count(keyframes) - count(vec_frames)`: see below.)
SELECT (SELECT COUNT(*) FROM vec_chunks WHERE chunk_id    NOT IN (SELECT id FROM chunks)),
       (SELECT COUNT(*) FROM vec_frames WHERE keyframe_id NOT IN (SELECT id FROM keyframes));

-- 4. The FTS indexes are intact. These are WRITES — a `mode=ro` connection
--    refuses them, which is exactly how this check gets quietly skipped.
INSERT INTO cues_fts(cues_fts)             VALUES('integrity-check');
INSERT INTO ocr_frames_fts(ocr_frames_fts) VALUES('integrity-check');
INSERT INTO videos_fts(videos_fts)         VALUES('integrity-check');
```

**Do not use `index-schema.md` §3.3's drift subtraction here.**
`count(keyframes) - count(vec_frames)` is **legitimately non-zero on a healthy
corpus**: duplicate keyframes (`dup_of IS NOT NULL`) are stored but never
embedded, so on the live database it stands at ~3,800 and a removal moves it
without that meaning anything. The frame-side equivalence that *does* hold is
`count(keyframes WHERE dup_of IS NULL) == count(vec_frames)` — verified exact
after the rehearsal, 8,429 = 8,429 — and the orphan query above is the sharper
form of it, because it names the failure a takedown actually cares about: a
vector that survived the frame it belonged to.

Then the file side:

```bash
test ! -e "$DATA/keyframes/$ID" && echo "keyframes gone"
find "$DATA/keyframes" -type f -name '*.jpg' | wc -l    # == SELECT count(*) FROM keyframes
```

And the product side, which is the one that matters to the person who asked —
search for something only that talk said, through the running server, and get
nothing:

```bash
curl -s 'http://127.0.0.1:8100/api/search?q=<a+distinctive+phrase>' | jq '.results | length'
```

### 2.7 Reclaim the pages — the text is still in the file until you VACUUM

A SQLite `DELETE` frees pages; it does not scrub them. The transcript is still
recoverable from free space in `vidtheque.db` until the file is rewritten. For
a takedown that is the difference between "removed" and "not currently
returned":

```python
conn.execute("VACUUM")            # the §2.0 connection, server stopped
```

Do it with the server stopped, or take a `VACUUM INTO` snapshot and swap it in
(`index-schema.md` §5 — that is also the blessed way to copy the database at
all, since a bare file copy under WAL is a torn database). It shrinks the file,
which is how you confirm it happened.

### 2.8 Purge the edge cache — the tunnel is not the only copy

`/frames/*.jpg` is served `Cache-Control: public, max-age=86400` and `.jpg` is
default-cached by Cloudflare, so **stopping the origin does not un-publish the
keyframes** — edge copies answer for up to a day
(`BEFORE-SHIP.md`, rollback card, step 1). After §2.5:

> Cloudflare dashboard → **Caching → Configuration → Purge Everything**

### 2.9 Backups

`VACUUM` and `rm -rf` do not reach a snapshot. Whatever backup generations
exist (`~/backups/vidtheque-*`, `pct snapshot`, `vzdump`) still contain the
video, and one of them will eventually be restored. Either delete the
generations that contain it, or write down that the corpus must be re-cleaned
after any restore — and if the second, put it where the restore procedure is,
not only here.

### 2.10 Do not re-index it

Nothing in the schema records "this was removed on request", so the next
indexing tranche will happily add it back. Until §7's blocklist exists, the
protection is procedural: keep the removed ids in a file the operator reads
before queueing anything, and note the removal in the handoff for the day.

---

## 3. Say so publicly

The promise is "…and we say so publicly", which is a separate obligation from
the delete. When a channel or a talk comes out, record it where a reader can
find it — the repo's issue thread, or a line on the demo page's corpus
description. A silent removal keeps the sentence in the positioning contract
from being true.

---

## 4. What removal does **not** reach

Stated plainly, because pretending otherwise would be the same failure the
positioning work set out to avoid:

- **Copies already served.** Anything a visitor or an agent already fetched is
  theirs, exactly as for anything ever published on the web.
- **The original video.** vidtheque never hosted it. Removal here takes the
  index out of vidtheque; the talk stays wherever its creator put it, which is
  the whole point of the receipts.
- **Third-party mirrors and caches** we do not control.

---

## 5. Removing a *span*, not a video

Sometimes the ask is narrower — a name in the Q&A, a slide with an internal
URL, thirty seconds that should not have been recorded. There is no supported
operation for this today, and the honest answers are:

- **Remove the whole video** (§2). Blunt, complete, always available.
- **Remove the keyframes covering the span** — `DELETE FROM keyframes WHERE
  video_id = ? AND t_s BETWEEN ? AND ?` plus the matching JPEGs, which takes
  the OCR and the frame vectors with it by cascade. The transcript still
  carries whatever was *said*.
- **Remove the cues covering the span** — same shape on `cues`. But `chunks`
  are built from cues and hold their own copy of the text, so the chunk rows
  and their `vec_chunks` vectors have to go too, and the video's `text_embed`
  stage is then stale. Doable, fiddly, and easy to do incompletely.

Take the blunt option unless the creator specifically prefers a partial one,
and verify with §2.6's product-side search either way.

---

## 6. Related runbooks

- `docs/deploy-public.md` — going public; §1 is the audit gate, and the
  rollback card is the "stop serving it now" lever from §2.2.
- `docs/design/index-schema.md` §1, §2.3, §3.3, §6.2 — the cascade, the FTS
  triggers, the vector triggers, and the designed `delete_video` job.
- `SECURITY.md` — vulnerabilities, which are a different thing and a different
  channel.

---

## 7. The tooling that should exist

Recorded here rather than in a comment, because the procedure above is
sufficient but it is not *good*, and every manual step is a step that can be
half-done at speed:

1. **A `delete_video` job.** `jobs.kind` already permits `'delete'`,
   `index-schema.md` §6.2 already specifies the semantics (row first, then
   `keyframes/<id>/`, `derived/<id>/`, `audio/<id>.*`, `media/<id>.*`), and the
   runner raises `E_NOT_IMPLEMENTED`. This is the gap.
2. **A channel-level verb**, since the promise is written about channels and
   the demo corpus is one channel.
3. **A removal blocklist**, so §2.10 is enforced rather than remembered.
4. **A `--verify` mode** that runs §2.6 and refuses to report success on
   non-zero drift.

*Addendum to `BEFORE-SHIP.md` G4b: the gate is met by this document — a
documented manual procedure was the agreed sufficient scope — and items 1–4
above are the follow-up work it defers.*
