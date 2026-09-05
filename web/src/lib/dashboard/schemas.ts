// The wire contract of `/dashboard/api/*` (docs/design/dashboard.md §19,
// docs/design/frontend-migration.md §§4-7), declared once as Zod schemas the
// TypeScript types are inferred from.
//
// Two things this file is careful about, and they are the same thing twice.
//
// **The projection redacts by omission, and omission is `null`.** In
// `VIDTHEQUE_PUBLIC_READONLY=1` the reads behind the operator's box are never
// taken, so `declared_models`, `storage` and `readiness.worker` arrive as
// `null` rather than absent or empty (§7). They are `.nullable()` here, and a
// page that renders one must have a designed absent state for it — a panel
// that is not there, never the word "null" on a screen.
//
// **A clock the corpus does not have is `null` too.** `published.oldest`,
// `last_indexed` and an arrival's `indexed_at` are all missing on an empty or
// half-built corpus, which is exactly the corpus an operator is looking at
// while they wonder why. `NaN` on that screen would be the worst possible
// answer.
//
// `z.object` strips unknown keys, which is the forward-compatible reading: a
// field Python adds later is ignored here until this file asks for it.
import { z } from "zod";

// Zod compiles its validators with `new Function` when it can, and probes for
// that by calling `Function("")` on first parse. These schemas parse in the
// *browser*, under a `script-src` with no `'unsafe-eval'` (see `proxy.ts`), so
// the probe is a guaranteed CSP violation in the console for a feature that
// was always going to fall back. `jitless` makes the fallback the decision.
z.config({ jitless: true });

const epoch = () => z.number().int();
const seconds = () => z.number();
const count = () => z.number().int();

// --------------------------------------------------------------- readiness

// One observation of the pipeline, never a history (dashboard.md §15). Its
// clock is epoch seconds like every other clock in these payloads, so the page
// and the payload cannot name different seconds.
export const WorkerModel = z.object({
  task: z.string(),
  model: z.string(),
  loaded: z.boolean(),
});
export type WorkerModel = z.infer<typeof WorkerModel>;

export const Worker = z.object({
  // `ready | unavailable | unconfigured` today, and a string rather than an
  // enum so a word the worker grows later renders instead of failing the
  // parse. The tone map falls back to neutral on anything it does not know,
  // which is dashboard.md §4.5's rule for exactly this.
  state: z.string(),
  detail: z.string(),
  models: z.array(WorkerModel),
});
export type Worker = z.infer<typeof Worker>;

export const Readiness = z.object({
  mcp: z.string(),
  database: z.string(),
  vectors: z.object({
    enabled: z.boolean(),
    // Why the vector legs are off — a dimension or model mismatch written for
    // whoever set the env. The projection drops the sentence and keeps
    // `enabled`, because search answers differently without those legs (§7).
    reason: z.string().nullable(),
  }),
  // `null` in the projection: the probe was not made at all.
  worker: Worker.nullable(),
  checked_at: epoch(),
});
export type Readiness = z.infer<typeof Readiness>;

export const Storage = z.object({
  keyframe_bytes: count(),
  database_bytes: count(),
});
export type Storage = z.infer<typeof Storage>;

// ---------------------------------------------------------------- overview

export const Overview = z.object({
  counted_at: epoch(),
  redacted: z.boolean(),
  corpus: z.object({
    videos: count(),
    queryable_videos: count(),
    // The store's own state words as keys, only the states that are present.
    videos_by_index_state: z.record(z.string(), count()),
    // `corpus-summary`'s word, printed verbatim: the four state vocabularies
    // are not unified and this surface does not invent a fifth.
    data_status: z.string(),
    cues: count(),
    keyframes: count(),
    ocr_lines: count(),
    // Seconds, not hours: `corpus_rollup`'s `hours` is a display rounding and
    // is deliberately not on the wire, so the rounding is React's to do once.
    duration_s: seconds(),
    published: z.object({ oldest: epoch().nullable(), newest: epoch().nullable() }),
    last_indexed: epoch().nullable(),
  }),
  // Capped server-side at 12 and 24; the order is the rollup's, most-used
  // first, which is why tags are a list and not an object.
  channels: z.array(z.object({ channel: z.string(), videos: count(), seconds: seconds() })),
  tags: z.array(z.object({ tag: z.string(), videos: count() })),
  gaps: z.object({
    transcript_no_ocr: count(),
    indexing: count(),
    // A count of failed videos. The rows behind it carry the pipeline's prose
    // about the operator's box and reach no surface from here.
    failed: count(),
  }),
  embed_backlog: z.object({ text: count(), frame: count() }),
  jobs: z.object({
    active: count(),
    running: count(),
    deferred: count(),
    failed_recent: count(),
    // The window the count was taken over, so the page's sentence and the
    // query behind it cannot disagree.
    failed_window_s: count(),
  }),
  recent: z.array(
    z.object({
      video_id: z.string(),
      title: z.string(),
      channel: z.string(),
      duration_s: seconds().nullable(),
      indexed_at: epoch().nullable(),
      // A same-origin `/frames/...` path, signed on the owner's instance and
      // bare in the projection. `null` when the video has no keyframe yet.
      thumb: z.string().nullable(),
    }),
  ),
  readiness: Readiness,
  // Both `null` in the projection (§7): checkpoint ids and byte totals answer
  // "how was this box configured", which is not what the corpus holds.
  declared_models: z
    .array(z.object({ label: z.string(), key: z.string(), value: z.string(), dim: z.string() }))
    .nullable(),
  storage: Storage.nullable(),
});
export type Overview = z.infer<typeof Overview>;

// ------------------------------------------------------------------ ledger

export const Ledger = z.object({
  counted_at: epoch(),
  redacted: z.boolean(),
  corpus: z.object({
    videos: count(),
    duration_s: seconds(),
    cues: count(),
    keyframes: count(),
    ocr_lines: count(),
    chunks: count(),
    tags: count(),
    channels: count(),
    // The span the band prints under the video count, and deliberately the
    // overview's field: same name, same shape, off the same rollup, because
    // one fact with two spellings is how the two pages start disagreeing
    // about the corpus. Both halves are `null` on an empty one.
    published: z.object({ oldest: epoch().nullable(), newest: epoch().nullable() }),
    last_indexed: epoch().nullable(),
  }),
  // Sums to `corpus.videos` by construction.
  videos_by_state: z.object({
    ready: count(),
    pending: count(),
    indexing: count(),
    failed: count(),
    stale: count(),
  }),
  jobs_by_state: z.object({
    queued: count(),
    running: count(),
    done: count(),
    failed: count(),
    cancelled: count(),
  }),
  queue: z.object({
    active: count(),
    running: count(),
    deferred: count(),
    failed_recent: count(),
    failed_window_s: count(),
  }),
  embed_backlog: z.object({ text: count(), frame: count() }),
  gaps: z.object({ transcript_no_ocr: count() }),
  readiness: Readiness,
  storage: Storage.nullable(),
});
export type Ledger = z.infer<typeof Ledger>;

// ----------------------------------------------------------------- session

// Readable signed out, deliberately (§6): a shell that cannot ask this can only
// guess whether to render a dashboard or a sign-in link.
export const Session = z.object({
  version: z.string(),
  // `none | token | oauth`, a string rather than an enum for the same reason
  // the worker's state is one.
  auth_mode: z.string(),
  readonly: z.boolean(),
  write_side: z.boolean(),
  writes_allowed: z.boolean(),
  authenticated: z.boolean(),
  is_owner: z.boolean(),
  // The *validated* session row, never the cookie's presence: "will the next
  // request be served".
  signed_in: z.boolean(),
  // The cookie's mere presence, which authorizes nothing: "is there a cookie
  // to clear". A stale cookie reads `true` here and `false` above, and that
  // pair is why the rail offers Sign out on either.
  //
  // Optional with a `false` default: it is the newest field in this payload
  // and this shell must render against an instance that predates it.
  has_session_cookie: z.boolean().optional().default(false),
  policy: z.string(),
  // `null` where this deployment registers no write side, which is also where
  // `/dashboard/login` is not routed — a read-only instance that still gates
  // its reads refuses without having anywhere to send the reader.
  login_url: z.string().nullable(),
  sign_in_hint: z.string().nullable(),
  accepts_password: z.boolean(),
  accepts_token: z.boolean(),
});
export type Session = z.infer<typeof Session>;

// ----------------------------------------------------------------- library

// The videos table and the video detail (dashboard.md §20). Two payloads at
// one prefix, and the name is `library` rather than `videos` because
// `/dashboard/api/videos` is already the public facade's listing here — one
// path cannot carry two contracts.

/** A clock the store does not have. Every date on a video is nullable: a video
 *  mid-pipeline has no `indexed_at`, and a source that gave no upload date has
 *  no `published_at`. `format.day` and `format.at` print the dash for both. */
const clockOf = () => epoch().nullable();

// A tool's typed refusal, in its own shape: `code`, not `error`. It is what
// `video-summary` answers a half-indexed video with, and it rides *on* the
// detail payload rather than replacing it — the panels below it are thin
// because of it, so it is a fact about the video and not a failed read.
export const ToolError = z.object({
  code: z.string(),
  message: z.string(),
  next: z.string().nullable(),
  retry_after_s: z.number().nullable().optional(),
});
export type ToolError = z.infer<typeof ToolError>;

export const LibraryRow = z.object({
  video_id: z.string(),
  title: z.string(),
  channel: z.string(),
  published_at: clockOf(),
  duration_s: seconds().nullable(),
  indexed_at: clockOf(),
  // The schema's own word — `pending|indexing|ready|failed|stale` — as a
  // string rather than an enum, so a state the store grows renders in the
  // neutral tone instead of failing the parse.
  index_state: z.string(),
  // The `t/o/f` letters as the three booleans they were computed from. The
  // letters are a text device for the `tsv` block a model reads, and this wire
  // promises typed values.
  coverage: z.object({ transcript: z.boolean(), ocr: z.boolean(), frames: z.boolean() }),
  tags: z.array(z.string()),
  // A root-relative `/frames/…` URL at the width it is displayed at, signed on
  // the owner's instance and bare in the projection. `null` without a keyframe.
  thumb: z.string().nullable(),
  link: z.string(),
});
export type LibraryRow = z.infer<typeof LibraryRow>;

export const Library = z.object({
  counted_at: epoch(),
  // `true` on a demo instance, and nothing on this payload is dropped for it:
  // §2.4 gives the demo the browsable corpus whole (§20's redaction table).
  redacted: z.boolean(),
  // Explicit, never inferred from the presence of `q`.
  order: z.string(),
  // The filters the query actually ran with, resolved to epochs — which is
  // what the date controls are seeded from, because the server clamps each
  // bound and snaps it to a UTC day before filtering and a box showing the raw
  // URL would name a filter that never ran. `_before` is *exclusive*, the
  // start of the day after the one asked for, so it is read back a day earlier
  // to get the date the reader typed.
  filters: z.object({
    q: z.string().nullable(),
    channel: z.string().nullable(),
    tags: z.array(z.string()),
    has: z.string(),
    index_state: z.string(),
    published_after: clockOf(),
    published_before: clockOf(),
    indexed_after: clockOf(),
    indexed_before: clockOf(),
  }),
  videos: z.array(LibraryRow),
  pagination: z.object({
    limit: count(),
    offset: count(),
    has_more: z.boolean(),
    // Only when the offset asked for ran past the end: where the last page
    // starts, so a reader who paged off it has somewhere to click.
    last_offset: count().optional(),
  }),
  // Exact, and deliberately not the tool's `~` probe (§5.2): a tilde over a
  // table with a Next button is the one thing on the line nobody can act on.
  total: count(),
  // Policy text — what a clamp moved, which value answered for an unknown one.
  // Rendered, never composed here.
  notes: z.array(z.string()),
});
export type Library = z.infer<typeof Library>;

// ------------------------------------------------------------ video detail

// One of the seven `video_stages` rows. `absent` is a state like any other and
// means the stage never ran, which is a different fact from a stage that ran
// and produced nothing — so all seven arrive and all seven render.
//
// `model_key` and `error` are the two fields the projection nulls (§20): a
// declared model id is a setting, and the error is the pipeline quoting
// yt-dlp — cookiefile paths, player clients, the operator's own box. A page
// that renders them needs a designed absent state, which here is the column
// not being drawn at all.
export const Stage = z.object({
  stage: z.string(),
  state: z.string(),
  model_key: z.string().nullable(),
  stage_version: count().nullable(),
  started_at: clockOf(),
  finished_at: clockOf(),
  error: z.string().nullable(),
});
export type Stage = z.infer<typeof Stage>;

// One line the machine read off a keyframe, and where it read it. The box is
// normalised 0–1 at write time (`pipeline/store.py`), so drawing it over the
// still costs nothing — and it is the difference between "OCR ran" and "here
// is what it read, and where".
export const OcrLine = z.object({
  line_no: count(),
  text: z.string(),
  conf: z.number().nullable(),
  box: z.tuple([z.number(), z.number(), z.number(), z.number()]),
});
export type OcrLine = z.infer<typeof OcrLine>;

export const FrameCard = z.object({
  frame_id: z.string(),
  ord: count(),
  t_s: seconds(),
  shot_id: count(),
  sharpness: z.number().nullable(),
  width: count().nullable(),
  height: count().nullable(),
  jpeg_bytes: count().nullable(),
  ocr_state: z.string(),
  /** The ordinal of the frame this one duplicates, when it was deduplicated. */
  dup_of_ord: count().nullable(),
  // The three widths of §6.4's derived cache. Never inline base64: a page of
  // forty base64 JPEGs is the byte analogue of the token blowup that invariant
  // exists to prevent.
  thumb: z.string(),
  detail: z.string(),
  large: z.string(),
  lines: z.array(OcrLine),
});
export type FrameCard = z.infer<typeof FrameCard>;

// The cut structure, as facts. No percentages on the wire: a bar's `left` and
// `width` are a rendering of these seconds against the video's runtime, and
// all three numbers are on the payload (§20, "Percentages are not sent").
export const Shot = z.object({
  shot_id: count(),
  start_s: seconds(),
  end_s: seconds(),
  frames: count(),
  kept: count(),
  ocr_done: count(),
  first_ord: count(),
  preview: z.string().nullable(),
});
export type Shot = z.infer<typeof Shot>;

export const VideoDetail = z.object({
  fetched_at: epoch(),
  redacted: z.boolean(),
  video: z.object({
    video_id: z.string(),
    title: z.string(),
    channel: z.string(),
    published_at: clockOf(),
    duration_s: seconds().nullable(),
    language: z.string(),
    index_state: z.string(),
    indexed_at: clockOf(),
    added_at: clockOf(),
    url: z.string(),
    description: z.string(),
    tags: z.array(z.string()),
  }),
  // `video-summary`'s own word, printed verbatim (§4.5): the four state
  // vocabularies are deliberately not unified and this surface invents no
  // fifth. `null` when the summary refused instead of answering.
  data_status: z.string().nullable(),
  summary_error: ToolError.nullable(),
  chapters: z.array(
    z.object({ start_s: seconds(), title: z.string(), link: z.string().nullable() }),
  ),
  stages: z.array(Stage),
  counts: z.object({
    cues: count(),
    cues_with_words: count(),
    chunks: count(),
    chapters: count(),
    keyframes: count(),
    keyframes_kept: count(),
    ocr_frames: count(),
    ocr_lines: count(),
    jpeg_bytes: count(),
  }),
  // `{origin: n}` — how a human sees that three of fifty-seven videos came in
  // through captions rather than through the transcriber.
  cue_origins: z.record(z.string(), count()),
  // A pointer, not a copy: the totals the panel's header prints, and the name
  // and the bounds of the endpoint that serves the cues themselves.
  transcript: z.object({
    cues: count(),
    words: count(),
    chars: count(),
    endpoint: z.string(),
    default_limit: count(),
    max_limit: count(),
  }),
  shots: z.object({ shots: z.array(Shot), capped: z.boolean(), cap: count() }),
  frames: z.object({
    frames: z.array(FrameCard),
    limit: count(),
    offset: count(),
    has_more: z.boolean(),
    // The honest half of the double cap: when the page's line budget is spent
    // the per-frame lists under-report, and the panel says so.
    ocr_line_cap: count(),
    ocr_lines_capped: z.boolean(),
  }),
  job_history: z.object({
    jobs: z.array(
      z.object({
        job_id: z.string(),
        state: z.string(),
        kind: z.string(),
        created_at: clockOf(),
        finished_at: clockOf(),
        error_code: z.string().nullable(),
        degraded_stages: z.array(z.string()),
      }),
    ),
    cap: count(),
  }),
  notes: z.array(z.string()),
});
export type VideoDetail = z.infer<typeof VideoDetail>;

// -------------------------------------------------------------- transcript

// `GET /dashboard/api/videos/{video_id}/cues` — the one read these pages make
// that predates the JSON slice. It has served the Jinja scrollbox since
// 2026-08-10, and it answers in *two* halves.
//
// The typed half (`start_s`, `end_s`, `avg_logprob`, `chunk_opens`,
// `chunk_closes`) is what decision 5 asks for and what this page renders from.
// The rendered half (`at`, `conf`, `chunk`) is what the endpoint has always
// sent, for a script that carries no formatter of its own.
//
// **The typed fields are optional and the strings are the fallback**, so this
// shell renders against an instance that predates the addition. When the
// strings are cut from the endpoint, `at`, `conf` and `chunk` come out of here
// and the three fallbacks in `Transcript` go with them.
export const Cue = z.object({
  start_s: seconds().optional(),
  end_s: seconds().optional(),
  avg_logprob: z.number().nullable().optional(),
  chunk_opens: z
    .object({
      seq: count(),
      start_s: seconds(),
      end_s: seconds(),
      n_chars: count(),
      n_words: count(),
    })
    .nullable()
    .optional(),
  chunk_closes: z.boolean().optional(),
  at: z.string(),
  t: count(),
  text: z.string(),
  speaker: z.string().nullable(),
  conf: z.string().nullable(),
  // The two markers collapsed into one bool: a chunk's last cue is not its
  // first, and the panel draws them differently.
  in_chunk: z.boolean(),
  chunk: z.string().nullable(),
});
export type Cue = z.infer<typeof Cue>;

export const CuePage = z.object({
  cues: z.array(Cue),
  offset: count(),
  limit: count(),
  // `has_more`, never a total: the panel's own "of N" is on the detail
  // payload, which it has already read.
  has_more: z.boolean(),
});
export type CuePage = z.infer<typeof CuePage>;

// ------------------------------------------------------------------ errors

// The refusal envelope every gate and every tool error on this surface answers
// with: `{"error", "message", "next"}` at the status `errors.HTTP_STATUS` maps
// the code to. Partial, because a proxy's HTML 502 is still an error this
// client has to carry.
export const PartialRefusal = z.object({
  error: z.string().optional(),
  message: z.string().optional(),
  next: z.string().nullable().optional(),
});
export type PartialRefusal = z.infer<typeof PartialRefusal>;

// ------------------------------------------------------------------- jobs

// `GET /dashboard/api/jobs` and `/dashboard/api/jobs/{job_id}` — the two poll
// targets `static/jobs.js` has read since phase 2, and the oldest JSON on this
// surface (dashboard.md §5.4).
//
// **They answer in two halves, and this file reads one of them.** Beside every
// typed field is a `text` block the server rendered for a script that carries
// no formatter — `progress`, `counts`, `tally`, `wall`, `ran`, `waited`,
// `defer`, `finished`, and per item `attempts`, `took`, `stage`. Every one of
// those is a rendering of a number that is already here, so these pages render
// from the numbers and read none of the strings: that is decision 5, and the
// strings are cut in the commit that deletes `static/jobs.js`
// (frontend-migration.md §3).
//
// `basis` is the exception the contract names. It is not a rendering of
// anything — it is the sentence saying what the percentage is *computed over*,
// which is policy text and stays Python's. It survives that cut "in `notes` or
// beside it", so it is read from both places and both are optional: this shell
// works against the instance that still nests it under `text`, and against the
// one that has moved it out.
const basisOf = () => z.object({ basis: z.string().optional() }).nullable().optional();

// What a job *contains*, from its own items: the first video's title with the
// rest counted after it, and the channel a batch was expanded from when every
// resolved item came from one.
//
// **Optional, because the poll target does not send it today.** The Jinja page
// reads it in a separate grouped query and deliberately keeps it off the tick —
// what a job contains does not change between two ticks — so
// `/dashboard/api/jobs` carries no title at all and a React row falls back to
// the item count it does have. The day the payload grows the field, the
// fallback stops being reached and nothing else moves.
export const JobContents = z.object({
  title: z.string().nullable(),
  more: count(),
  channel: z.string().nullable(),
  note: z.string().nullable(),
});
export type JobContents = z.infer<typeof JobContents>;

export const JobCard = z.object({
  job_id: z.string(),
  // The store's own words — `queued|running|done|failed|cancelled` — as
  // strings, so a state the queue grows renders neutral instead of failing.
  state: z.string(),
  kind: z.string(),
  priority: count(),
  // Already a whole percent: `_job_card` rounds `jobs.progress` server-side.
  progress: count(),
  n_items: count(),
  n_done: count(),
  n_failed: count(),
  n_skipped: count(),
  n_cancelled: count(),
  cancel_requested: z.boolean(),
  created_at: epoch(),
  started_at: clockOf(),
  finished_at: clockOf(),
  // The three durations, and which of them are `null` is the fact: a job that
  // was never claimed has waited and has not run (dashboard.md §5.4).
  waited_s: count().nullable(),
  ran_s: count().nullable(),
  wall_s: count().nullable(),
  // `queued|running` — the store's own liveness, and the poll's stop condition.
  live: z.boolean(),
  // Seconds of `not_before` still to run, and `0` on anything but a queued
  // job: the stamp on a running row is what the last deferral left behind, and
  // a countdown against it would invent a wait that is not happening.
  defer_s: count(),
  error_code: z.string().nullable(),
  // `null` in the projection: the pipeline quoting yt-dlp about the operator's
  // own box (§2.4).
  error_message: z.string().nullable(),
  // How many of this job's `done` items have a failed stage underneath them.
  degraded: count(),
  contents: JobContents.optional(),
  basis: z.string().optional(),
  text: basisOf(),
});
export type JobCard = z.infer<typeof JobCard>;

export const JobItem = z.object({
  item_id: count(),
  seq: count(),
  state: z.string(),
  stage: z.string().nullable(),
  stage_pct: count(),
  attempts: count(),
  max_attempts: count(),
  // The half of "will this retry" a row can actually answer:
  // `ItemFailed.retryable` is not persisted (§4.4).
  retries_left: count(),
  video_id: z.string().nullable(),
  title: z.string().nullable(),
  channel: z.string().nullable(),
  duration_s: seconds().nullable(),
  // `null` in the projection — the submitted URL is `args_json` by another
  // name. The video it resolved to is not, and stays.
  source_url: z.string().nullable(),
  error_code: z.string().nullable(),
  error_message: z.string().nullable(),
  started_at: clockOf(),
  finished_at: clockOf(),
  took_s: count().nullable(),
  text: basisOf(),
});
export type JobItem = z.infer<typeof JobItem>;

export const JobEvent = z.object({
  id: count(),
  at: epoch(),
  level: z.string(),
  stage: z.string().nullable(),
  item_id: count().nullable(),
  // `null` in the projection: the runner writes yt-dlp's string into it and a
  // reclaim writes the item's URL, and there is no structured half to keep.
  message: z.string().nullable(),
});
export type JobEvent = z.infer<typeof JobEvent>;

export const Jobs = z.object({
  now: epoch(),
  // The server's own cadence. Clamped again in the browser: a page that took
  // its interval from a payload alone would poll as fast as a payload said.
  poll_ms: count(),
  // Is anything `queued|running`? When nothing is, there is nothing to poll
  // for and the tab stops being a load generator against the process that
  // also holds the only SQLite writer.
  live: z.boolean(),
  jobs: z.array(JobCard),
  pagination: z.object({ limit: count(), offset: count(), has_more: z.boolean() }),
});
export type Jobs = z.infer<typeof Jobs>;

// One job's war story. Six of the things the Jinja page renders are assembled
// in `views._job_detail` and **not sent on this payload** — `counts`,
// `error_counts`, `degraded`, `focus`, `stages` and `items_capped` — so each is
// optional here and the panel that renders it is absent rather than empty
// until the payload carries it.
export const JobDetail = z.object({
  now: epoch(),
  poll_ms: count(),
  live: z.boolean(),
  job: JobCard,
  items: z.array(JobItem),
  events: z.array(JobEvent),
  items_capped: z.boolean().optional(),
  // `{state: n}` over every item — the tally the job card's own five counts
  // already carry, read only if it arrives.
  counts: z.record(z.string(), count()).optional(),
  error_counts: z.record(z.string(), count()).optional(),
  degraded: z
    .array(
      z.object({
        seq: count(),
        video_id: z.string().nullable(),
        stage: z.string(),
        error: z.string().nullable(),
      }),
    )
    .optional(),
  focus: JobItem.nullable().optional(),
  stages: z
    .array(
      z.object({
        stage: z.string(),
        state: z.string(),
        started_at: clockOf(),
        finished_at: clockOf(),
        took_s: count().nullable(),
      }),
    )
    .optional(),
});
export type JobDetail = z.infer<typeof JobDetail>;

// ------------------------------------------------------------- the writes

// The two typed outcomes this surface's controls read (dashboard.md §21).
// Values only — the store's own state word, ints, a boolean, a list — because
// the formatting is React's and the only policy text on a write is the
// refusal's, which travels in `PartialRefusal`.

/** `POST /dashboard/jobs/{job_id}/cancel`.
 *
 *  `state` is the state the job is in *now*, and which of the two it is, is the
 *  reason this route answers inline at all: queued work settles `cancelled`
 *  immediately, running work stays `running` with the request recorded until
 *  the pipeline reaches its next stage boundary. A 2 s poll cannot tell an
 *  operator which of those just happened. */
export const CancelOutcome = z.object({
  job_id: z.string(),
  state: z.string(),
  cancel_requested: z.boolean(),
});
export type CancelOutcome = z.infer<typeof CancelOutcome>;

/** `POST /dashboard/jobs/{job_id}/retry`.
 *
 *  Every new job, always as a list: the Jinja page goes straight to the new job
 *  when there is exactly one, and a client that always reads `jobs` is a client
 *  with no special case. `200` when anything was accepted, `409` when nothing
 *  was — and the `409` carries this same shape with the refusals in `errors`,
 *  so it is a payload to read rather than an error to throw. */
export const RetryOutcome = z.object({
  from_job_id: z.string(),
  selected: count(),
  jobs: z.array(z.object({ job_id: z.string(), items: count() })),
  errors: z.array(PartialRefusal),
  preserved: z.object({
    channels: z.string(),
    tags: z.array(z.string()),
    priority: z.string(),
  }),
});
export type RetryOutcome = z.infer<typeof RetryOutcome>;
