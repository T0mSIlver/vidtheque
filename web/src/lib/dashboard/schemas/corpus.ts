import { z } from "zod";
import { count, epoch, httpUrl, Readiness, seconds, Storage } from "./common";

const Published = z.object({ oldest: epoch().nullable(), newest: epoch().nullable() });

/** `GET /dashboard/api/overview` (dashboard.md §5.1, §19). */
export const Overview = z.object({
  counted_at: epoch(),
  redacted: z.boolean(),
  /** The session's flag, on this payload so the page paints it at once. */
  writes_allowed: z.boolean(),
  corpus: z.object({
    videos: count(),
    queryable_videos: count(),
    /** Ready only, not ready-plus-stale: with `videos` it adds up. */
    videos_ready: count(),
    videos_by_index_state: z.record(z.string(), count()),
    data_status: z.string(),
    cues: count(),
    keyframes: count(),
    ocr_lines: count(),
    /** Seconds: hours are a display rounding and are not on the wire. */
    duration_s: seconds(),
    published: Published,
    last_indexed: epoch().nullable(),
  }),
  channels: z.array(z.object({ channel: z.string(), videos: count(), seconds: seconds() })),
  tags: z.array(z.object({ tag: z.string(), videos: count() })),
  gaps: z.object({
    transcript_no_ocr: count(),
    indexing: count(),
    failed: count(),
    /** The probe's `LIMIT`, and whether the count reached it (`5+`). */
    failed_cap: count(),
    failed_capped: z.boolean(),
  }),
  embed_backlog: z.object({ text: count(), frame: count() }),
  jobs: z.object({
    active: count(),
    running: count(),
    deferred: count(),
    failed_recent: count(),
    failed_window_s: count(),
  }),
  recent: z.array(
    z.object({
      video_id: z.string(),
      title: z.string(),
      channel: z.string(),
      duration_s: seconds().nullable(),
      indexed_at: epoch().nullable(),
      thumb: httpUrl().nullable(),
    }),
  ),
  readiness: Readiness,
  // `null` in the projection (§7).
  declared_models: z
    .array(z.object({ label: z.string(), key: z.string(), value: z.string(), dim: z.string() }))
    .nullable(),
  storage: Storage.nullable(),
});
export type Overview = z.infer<typeof Overview>;

/** `GET /dashboard/api/ledger` (dashboard.md §17). */
export const Ledger = z.object({
  counted_at: epoch(),
  redacted: z.boolean(),
  writes_allowed: z.boolean(),
  corpus: z.object({
    videos: count(),
    duration_s: seconds(),
    cues: count(),
    keyframes: count(),
    ocr_lines: count(),
    chunks: count(),
    tags: count(),
    channels: count(),
    published: Published,
    last_indexed: epoch().nullable(),
  }),
  /** Sums to `corpus.videos`. */
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
