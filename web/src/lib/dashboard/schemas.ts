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

// `static/dashboard.js`'s `safeUrl()`, as a schema: **only `http:` and
// `https:` ever reach an `href` or a `src`.** `javascript:` and `data:` are
// URLs by the parser's reckoning and scripts by the browser's, and nothing on
// this surface mints either — a video's link, a follow's source, a job item's
// submitted URL and every frame path are all written by Python from the store,
// so a payload that says otherwise is a contract break and is refused here
// rather than handed to the DOM.
//
// It is `lib/api/schemas.ts`'s `httpUrl()` with one difference, and the
// difference is this surface's: half of these are **root-relative paths** on
// the instance's own origin — `/frames/…`, `/dashboard/login` — so the value is
// resolved against a base before its scheme is read, exactly as the deleted
// `safeUrl()` resolved against `window.location.href`. That makes a bare path
// http(s) and leaves `javascript:alert(1)` what it is.
const BASE = "http://dashboard.invalid/";

function isHttpUrl(value: string): boolean {
  try {
    const { protocol } = new URL(value, BASE);
    return protocol === "http:" || protocol === "https:";
  } catch {
    return false;
  }
}

/** An absolute `http(s)` URL, or a path on this instance's own origin. */
const httpUrl = () => z.string().refine(isHttpUrl, "must be an http(s) URL or a same-origin path");

/**
 * A path on this instance and never an origin of somebody else's choosing.
 *
 * `login_url` is the one URL on these payloads a *refusal* sends a browser to,
 * and it is written as a path (`/dashboard/login`). `//host` and `/\host` are
 * absolute URLs wearing a path's clothes — they resolve to http(s) and would
 * pass `httpUrl()` — so this is the tighter fence `writes._safe_next` and
 * `LoginView.safeNext` apply to `next`, said here for the field that carries a
 * destination. An absolute http(s) URL is still allowed: an instance behind a
 * proxy may name its own front door.
 */
const localUrl = () =>
  z
    .string()
    .refine(
      (value) => isHttpUrl(value) && !value.startsWith("//") && !value.startsWith("/\\"),
      "must be a same-origin path or an http(s) URL",
    );

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
    // Ready **only**, and it is deliberately not `queryable_videos`: that is
    // ready *plus* stale, and a stale video answers a query without being
    // ready. The band's "N ready" is this number and its "not ready" is
    // `videos` minus this number — `corpus_rollup`'s own two columns, which
    // add up to the corpus by construction.
    videos_ready: count(),
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
    // …and whether that count is a ceiling. `queries.gaps` probes with a
    // `LIMIT` and reports the length, so at the cap it means "this many or
    // more" and the page prints `5+`. The number and the reading of it both
    // travel: a `5` written into the page is how a cap gets reported as an
    // exact count the day the `LIMIT` changes.
    failed_cap: count(),
    failed_capped: z.boolean(),
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
      thumb: httpUrl().nullable(),
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
  /** Why this database refuses writes, in the instance's own words.
   *
   *  `Database._assert_dimensions` turns `writes_allowed` off and writes the
   *  sentence in the same breath, and the index form printed that sentence
   *  under its disabled controls — a form refused with no reason is a form an
   *  operator retypes. Policy text, `null` where writes are allowed and `null`
   *  in the projection, which is the readiness block's rule for the same
   *  string. Optional, like `has_session_cookie`: this shell renders against
   *  an instance that predates the field. */
  writes_refused_reason: z.string().nullable().optional().default(null),
  policy: z.string(),
  // `null` where this deployment registers no write side, which is also where
  // `/dashboard/login` is not routed — a read-only instance that still gates
  // its reads refuses without having anywhere to send the reader.
  login_url: localUrl().nullable(),
  sign_in_hint: z.string().nullable(),
  accepts_password: z.boolean(),
  accepts_token: z.boolean(),
});
export type Session = z.infer<typeof Session>;

/** What `POST /dashboard/login` answers a client with (dashboard.md §21).
 *
 *  `next` is where the reader was going, already fenced to this surface by
 *  `writes._safe_next` — and fenced again by the page before it navigates: a
 *  redirect target that arrived over the wire is an input, and the page that
 *  mints the session cookie is the worst place on this surface to have an open
 *  redirect. The cookie itself is on the response's `Set-Cookie` and is
 *  `HttpOnly`, so nothing here can see it and nothing here has to. */
export const SignedIn = z.object({
  signed_in: z.boolean(),
  next: z.string(),
});
export type SignedIn = z.infer<typeof SignedIn>;

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
  thumb: httpUrl().nullable(),
  link: httpUrl(),
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
  thumb: httpUrl(),
  detail: httpUrl(),
  large: httpUrl(),
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
  preview: httpUrl().nullable(),
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
    url: httpUrl(),
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
// that predates the JSON slice. It has served the transcript scrollbox since
// 2026-08-10, and it answers in numbers.
//
// It used to answer in two halves: these five typed fields beside `at`, `conf`
// and `chunk`, three strings pre-rendered for a script that carried no
// formatter of its own. That script went with the Python pages on 2026-09-06
// and the strings went with it (dashboard.md §23), so the typed half is
// required here and the timecode, the log-probability and the chunk label are
// this page's to compose. `t` stays: it is the whole-second start a `?t=`
// deeplink takes, not a rendering. A payload from an instance that still sends
// the strings parses — Zod strips what this object does not name.
export const Cue = z.object({
  start_s: seconds(),
  end_s: seconds(),
  avg_logprob: z.number().nullable(),
  chunk_opens: z
    .object({
      seq: count(),
      start_s: seconds(),
      end_s: seconds(),
      n_chars: count(),
      n_words: count(),
    })
    .nullable(),
  chunk_closes: z.boolean(),
  t: count(),
  text: z.string(),
  speaker: z.string().nullable(),
  // The two markers collapsed into one bool: a chunk's last cue is not its
  // first, and the panel draws them differently.
  in_chunk: z.boolean(),
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
// targets `static/jobs.js` read since phase 2, and the oldest JSON on this
// surface (dashboard.md §5.4).
//
// They used to answer in two halves: beside every typed field, a `text` block
// the server rendered for that script, which carried no formatter —
// `progress`, `counts`, `tally`, `wall`, `ran`, `waited`, `defer`, `finished`,
// and per item `attempts`, `took`, `stage`, plus each event's `at_text`. Every
// one was a rendering of a number sent beside it, so they went with the script
// on 2026-09-06 (dashboard.md §23) and nothing here ever read them.
//
// `basis` is the exception the contract named, and it survived as a field on
// the card: not a rendering of anything, but the sentence saying what the
// percentage is *computed over*, which is policy text and stays Python's. It
// is optional because it moved — an instance that predates that cut nests it
// under `text`, where this shell no longer looks, and a card without it draws
// the tally alone.

// What a job *contains*, from its own items: the first video's title with the
// rest counted after it, and the channel a batch was expanded from when every
// resolved item came from one.
//
// It rides on the poll target now, folded into the page's grouped row-facts
// read so the tick still costs two reads (dashboard.md §5.4). **Optional all
// the same**: an instance that predates that landing sends no title at all,
// and a React row falls back to the item count it does have.
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
  source_url: httpUrl().nullable(),
  error_code: z.string().nullable(),
  error_message: z.string().nullable(),
  started_at: clockOf(),
  finished_at: clockOf(),
  took_s: count().nullable(),
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

/** The projection this listing ran under, and the flag the Jinja pages gated
 *  their two footnotes on: with it true, `error_message` is `null` on every row
 *  because the deployment does not publish the prose, which is a different
 *  statement from a job that failed without one. Optional, so the shell renders
 *  against an instance that predates the field. */
const redactedFlag = () => z.boolean().optional();

export const Jobs = z.object({
  redacted: redactedFlag(),
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
  // The predicates the listing actually ran with, resolved — not the ones the
  // URL asked for. `state=nonsense` has always fallen back to `all`, and a
  // strip printing the URL's word over a table of every job is the page
  // vouching for a filter that never ran. `error_code` is `null` rather than
  // the form's empty string, like every other absent filter on this surface;
  // `degraded` is the boolean the query ran on, not the `1` that asked for it.
  // `limit` and `offset` stay in `pagination`, where the pager reads them.
  filters: z.object({
    state: z.string(),
    kind: z.string(),
    error_code: z.string().nullable(),
    degraded: z.boolean(),
    order: z.string(),
  }),
  // Policy text — a bound that moved, a value that fell back. Rendered, never
  // composed here: it is the `all` invariant reaching this payload, which has
  // no form to echo an accepted value back into (dashboard.md §5.4).
  notes: z.array(z.string()),
});
export type Jobs = z.infer<typeof Jobs>;

// One job's war story. Six of the things the Jinja page renders are assembled
// in `views._job_detail` and **not sent on this payload** — `counts`,
// `error_counts`, `degraded`, `focus`, `stages` and `items_capped` — so each is
// optional here and the panel that renders it is absent rather than empty
// until the payload carries it.
export const JobDetail = z.object({
  redacted: redactedFlag(),
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

/** `POST /dashboard/index` — the form that queues a batch.
 *
 *  The one thing the form does that the tool does not is a real batch: the MCP
 *  surface keeps its ten-URL cap and this route splits server-side instead
 *  (§10.7), so `batches` and `urls` are what makes a job count explicable — a
 *  split the operator cannot see is a job count they cannot account for.
 *
 *  It does not take the one-job redirect shortcut either: the page goes
 *  straight to the new job when there is exactly one, and a client that always
 *  reads `jobs` is a client with no special case. `200` when anything was
 *  accepted, `409` when nothing was — and the `409` carries this same shape
 *  with every refusal in `errors`, so it is a receipt to read rather than an
 *  error to throw.
 *
 *  `already_indexed` is video ids: `index_video` leaves a finished video alone
 *  unless the submission forced a rebuild, which is a fact about the corpus and
 *  not a failure. Each refusal carries the batch of URLs it was refused for,
 *  because a submission split into twenty jobs has twenty ways to fail
 *  partially. */
export const IndexOutcome = z.object({
  jobs: z.array(z.object({ job_id: z.string(), items: count(), urls: z.array(z.string()) })),
  already_indexed: z.array(z.string()),
  errors: z.array(PartialRefusal.extend({ urls: z.array(z.string()) })),
  batches: count(),
  urls: count(),
  /** What the server actually ran on, which is not always what was typed:
   *  `max_items` is clamped to the tool's own 1..200 and the two vocabularies
   *  fall back to their defaults rather than being refused. `_submitted` did
   *  this and the Jinja page re-rendered the form from it, so a reader who
   *  typed `max_items=9000` saw the 200 the batch used; a form that keeps its
   *  own state has nothing to read them back out of.
   *
   *  Optional because it rides on the *outcome*, and the two refusals this
   *  route has (`E_BAD_PARAM`, `E_TOO_LARGE`) are envelopes and carry none. */
  accepted: z.object({ expand: z.string(), max_items: count(), priority: z.string() }).optional(),
});
export type IndexOutcome = z.infer<typeof IndexOutcome>;

/** `POST /dashboard/videos/{video_id}/reindex` — force, one video.
 *
 *  `force_reindex` on this row's own URL with `expand=none`, so there is always
 *  exactly one job. `job_id` is nullable for the branch where the tool made
 *  none, which a forced rebuild does not reach — a page that assumed a string
 *  there would render `undefined` on the day it does. */
export const ReindexOutcome = z.object({
  video_id: z.string(),
  job_id: z.string().nullable(),
});
export type ReindexOutcome = z.infer<typeof ReindexOutcome>;

/** `POST /dashboard/videos/{video_id}/tags` — the row's tags *after* the write,
 *  read back.
 *
 *  Not what was added and removed: `tag_video` reports that across a batch, and
 *  it is not the question the panel that made the call is showing. Nothing
 *  asked for is nothing done on both branches, so a submission with neither
 *  field answers `200` with the tags unchanged. */
export const TagsOutcome = z.object({
  video_id: z.string(),
  tags: z.array(z.string()),
});
export type TagsOutcome = z.infer<typeof TagsOutcome>;

// -------------------------------------------------------------- following

// `GET /dashboard/api/following` and `/dashboard/api/following/{slug}`
// (dashboard.md §22, frontend-migration.md §6b) — §18's two pages, typed.
//
// **These two can be absent, and no other read on this surface can.** They are
// declared with the *write* routes because their pages are, so in
// `VIDTHEQUE_PUBLIC_READONLY=1` and in `VIDTHEQUE_AUTH=none` both answer `404`,
// page and JSON together. A `404` on the list therefore means "this deployment
// registers no write side", not "something went wrong" — the views ask
// `/api/session` for `write_side` before rendering the surface at all, and
// treat that `404` as the same answer arriving late.
//
// Three things the pages render that are deliberately **not** on these
// payloads, because each is a value composable from the columns beside it: the
// rule as compressed facts, the rule as an English sentence, and the near-miss
// line. `reason` is the one string that travels verbatim — it is the receipt
// the check wrote, carrying the number that made the decision, and re-deriving
// it on this side is how a receipt stops being one.

/** One follow, as every payload on this surface describes it.
 *
 *  The same block a write outcome answers with (§21) and a read lists (§22),
 *  built in Python from `Rules.from_row` — the parser the check itself uses —
 *  so pausing a follow and re-listing it cannot produce two shapes, and the
 *  payload and the check cannot disagree about what a CSV column meant.
 *
 *  Every rule column is on it, which is what lets the edit form be prefilled
 *  from the row a write just answered with rather than from a second read. */
export const FollowRow = z.object({
  slug: z.string(),
  title: z.string(),
  kind: z.string(),
  source_url: httpUrl(),
  // `active | paused | failing` — the store's own words, as strings, so a
  // state the schema grows renders neutral instead of failing the parse.
  state: z.string(),
  mode: z.string(),
  tabs: z.array(z.string()),
  // `all`, or a comma-joined subset: `index-video`'s own vocabulary rather
  // than a second one, so it is the string the tool wrote and not a list.
  channels: z.string(),
  tags: z.array(z.string()),
  min_duration_s: count().nullable(),
  max_duration_s: count().nullable(),
  title_include: z.array(z.string()),
  title_exclude: z.array(z.string()),
  backfill: count(),
  max_per_check: count(),
  check_interval_s: count(),
  // `0` is the state `Check now` leaves behind — due immediately, which is a
  // fact and not a missing clock. The other two are absent until a check has
  // run and until something has arrived.
  next_check_at: clockOf(),
  last_check_at: clockOf(),
  last_new_at: clockOf(),
});
export type FollowRow = z.infer<typeof FollowRow>;

/** The table's row: the shared block, plus the one column the table prints. */
export const FollowListRow = FollowRow.extend({
  last_error_code: z.string().nullable(),
});
export type FollowListRow = z.infer<typeof FollowListRow>;

/** The detail's row, which adds the prose beside the code.
 *
 *  `last_error_message` is `follows/check.py`'s `str(exc)[:400]` — the
 *  extractor quoted verbatim, the same category as a stage's error — and §22
 *  names it the first field that would have to go if these routes ever
 *  answered a projection. They answer none at all today. */
export const FollowDetailRow = FollowListRow.extend({
  last_error_message: z.string().nullable(),
});
export type FollowDetailRow = z.infer<typeof FollowDetailRow>;

export const Following = z.object({
  counted_at: epoch(),
  // `failing_first` — `list_follows`' one order, named rather than implied,
  // because a table read at 03:00 is read to find the follow that broke.
  order: z.string(),
  totals: z.object({
    follows: count(),
    active: count(),
    paused: count(),
    failing: count(),
    due_soon: count(),
    brought_in: count(),
    held: count(),
  }),
  budget: z.object({
    // Hours of *video*, each in the unit it is kept in: seconds spent, hours
    // configured. `ceiling_h: 0` means the operator turned the ceiling off,
    // which is a state and not "no budget left" — the band says so in words.
    spent_s: seconds(),
    ceiling_h: z.number(),
    // Rolling, not calendar, and on the wire so the band's sentence and the
    // query behind it cannot name two different windows.
    window_s: count(),
  }),
  // Two facts about the *deployment*, the shape `/api/session`'s `write_side`
  // already has, and neither names an environment variable.
  //
  // `checks_enabled` is the field the Jinja page has no line for, and it is
  // here because the clocks beside it are otherwise a lie: with follow checks
  // off, every `next_check_at` below is a time at which nothing will happen.
  checks_enabled: z.boolean(),
  // §5.5's honest refusal for the follow form: `follow_channel` refuses on the
  // same condition `index_video` does.
  vectors: z.boolean(),
  // …and the reason under that refusal, as the list's note printed it. One
  // `VectorState.reason` through one `drift_reason`, so the follow form and
  // the index form cannot be refused with two explanations. `null` where the
  // legs are on; optional, so this shell renders against an instance that
  // predates the field.
  vectors_reason: z.string().nullable().optional(),
  follows: z.array(FollowListRow),
  // Capped independently of the pager, because it is not what the pager pages:
  // at most `held_cap` candidates waiting on a *person*, across every follow.
  held: z.array(
    z.object({
      title: z.string(),
      url: httpUrl(),
      slug: z.string(),
      follow: z.string(),
      published_at: clockOf(),
      first_seen_at: clockOf(),
    }),
  ),
  held_more: z.boolean(),
  held_cap: count(),
  pagination: z.object({ limit: count(), offset: count(), has_more: z.boolean() }),
  notes: z.array(z.string()),
});
export type Following = z.infer<typeof Following>;

/** One `follow_check` or `index` job this follow owns, as an id and a state.
 *
 *  Never a copy of the job: the war story is already written at
 *  `/dashboard/jobs/{job_id}` and this band does not fork it. The two lists
 *  carry different halves of this shape, so what only one of them sends is
 *  optional here rather than duplicated into two near-identical schemas. */
export const FollowJob = z.object({
  job_id: z.string(),
  state: z.string(),
  error_code: z.string().nullable().optional(),
  n_items: count().optional(),
  n_done: count().optional(),
  n_failed: count().optional(),
  created_at: clockOf(),
  started_at: clockOf().optional(),
  finished_at: clockOf().optional(),
});
export type FollowJob = z.infer<typeof FollowJob>;

/** One candidate this follow decided not to index, and why. */
export const SeenRow = z.object({
  title: z.string(),
  url: httpUrl(),
  // One of the eight non-`queued` decisions, as the store's own word.
  decision: z.string(),
  // **Verbatim.** The receipt the check wrote, carrying the number that made
  // the call — "4:12, shorter than your 8:00 floor". Policy text, Python's.
  reason: z.string().nullable(),
  // `listing` or `probe`: whether measuring this candidate cost a request.
  judged_from: z.string(),
  duration_s: seconds().nullable(),
  published_at: clockOf(),
  decided_at: clockOf(),
});
export type SeenRow = z.infer<typeof SeenRow>;

export const FollowDetail = z.object({
  fetched_at: epoch(),
  follow: FollowDetailRow,
  // The same deployment fact the list sends, off the same `follow_settings`
  // and read through the same function, so the two endpoints cannot answer it
  // differently. The argument is stronger here than on the list (§22): this
  // page is *about* one follow's clock, and with checks off both `next_check_at`
  // and the in-flight line are a schedule nothing will run.
  checks_enabled: z.boolean(),
  brought_in: count(),
  // `{decision: n}` over every candidate this follow has ever judged, from one
  // grouped query, and only the decisions that are present.
  counts: z.record(z.string(), count()),
  // **`null` means print nothing.** The arithmetic is the contract and the
  // omission is part of it: a zero, or a follow with no length rule, sends
  // `null`, because "0 of the last 25" is a fact about nothing dressed as a
  // finding and this is the one band that has to stay believable. `within_s`
  // rides along so the count and the number in the sentence cannot disagree.
  near_miss: z
    .object({ count: count(), of: count(), within_s: count(), edge: z.string() })
    .nullable(),
  checks: z.array(FollowJob),
  index_jobs: z.array(FollowJob),
  // The check already queued or running, so `Check now` cannot look like it
  // did nothing.
  in_flight: z.string().nullable(),
  order: z.string(),
  seen: z.array(SeenRow),
  // Both job lists are bounded independently of `limit`, and the caps ride
  // along so this side can say "the 10 most recent" without hard-coding ten.
  caps: z.object({ checks: count(), index_jobs: count() }),
  pagination: z.object({ limit: count(), offset: count(), has_more: z.boolean() }),
  notes: z.array(z.string()),
});
export type FollowDetail = z.infer<typeof FollowDetail>;

// The six write outcomes (dashboard.md §21). Typed values only — the follow
// row, ints, booleans — because the formatting is this side's and the only
// policy text on a write is the refusal's, which travels in `PartialRefusal`.

// **The block on a write outcome is the *detail* row, not the bare one.**
// §21 builds it with `read_models.follow_row_json_with_error`, which is the
// same function §22's detail read calls — one function, so an outcome and a
// read cannot describe a follow two ways — and that block carries the two
// failure columns. They are on it because a `resume` *clears* them: `set_state`
// nulls both when it resumes, and an outcome that carried neither left the page
// rendering an error the write had just wiped. With them, the outcome is a
// complete row and the page needs no re-read to find that out.

/** `POST /dashboard/following` — the add form.
 *
 *  `already_following` is the tool's own field, and the difference a redirect
 *  cannot express: `action="follow"` on a URL already followed returns the
 *  existing follow and makes nothing, which is what makes a retried request
 *  safe. `follow` is nullable because the handler answers `null` when the tool
 *  gave it no slug to re-read. */
export const FollowCreated = z.object({
  follow: FollowDetailRow.nullable(),
  already_following: z.boolean(),
});
export type FollowCreated = z.infer<typeof FollowCreated>;

/** What `state`, `check` and `rules` answer: the row, **re-read after the
 *  write**. `set_state` re-arms the clock when it resumes, so a payload built
 *  from the row the handler read first would name the new state and the old
 *  `next_check_at` in one breath. */
export const FollowWritten = z.object({ follow: FollowDetailRow });
export type FollowWritten = z.infer<typeof FollowWritten>;

/** `POST /dashboard/following/{slug}/delete`.
 *
 *  `videos_kept` is the receipt for the asymmetry §18.5 asks this control to
 *  state: the rule and the ledger go, and the videos the follow brought in
 *  stay, because they are corpus and not membership. */
export const FollowDeleted = z.object({
  slug: z.string(),
  deleted: z.boolean(),
  videos_kept: count(),
});
export type FollowDeleted = z.infer<typeof FollowDeleted>;

/** `POST /dashboard/following/{slug}/queue` — "Index anyway", one row.
 *
 *  Both fields are nullable because nothing asked for is nothing done on both
 *  branches: a `queue` with no `url` answers `200` with the unchanged row
 *  rather than a refusal — the form's policy, not a second one written for a
 *  JSON caller. */
export const FollowQueued = z.object({
  slug: z.string(),
  url: httpUrl().nullable(),
  job_id: z.string().nullable(),
});
export type FollowQueued = z.infer<typeof FollowQueued>;
