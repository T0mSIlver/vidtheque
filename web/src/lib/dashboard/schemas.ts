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
