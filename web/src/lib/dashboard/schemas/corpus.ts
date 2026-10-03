import { z } from "zod";
import { count, epoch, Readiness, seconds, Storage } from "./common";

const Published = z.object({ oldest: epoch().nullable(), newest: epoch().nullable() });

/** `GET /dashboard/api/health` (dashboard.md §24.1, §19). */
export const Health = z.object({
  counted_at: epoch(),
  redacted: z.boolean(),
  /** The session's flag, on this payload so the page paints it at once. */
  writes_allowed: z.boolean(),
  data_status: z.string(),
  last_indexed: epoch().nullable(),
  gaps: z.object({
    transcript_no_ocr: count(),
    indexing: count(),
    /** Whether any video failed; the count is Corpus's. */
    has_failed: z.boolean(),
  }),
  embed_backlog: z.object({ text: count(), frame: count() }),
  jobs: z.object({
    active: count(),
    deferred: count(),
    failed_recent: count(),
    failed_window_s: count(),
  }),
  readiness: Readiness,
  // `null` in the projection (§7).
  declared_models: z
    .array(z.object({ label: z.string(), key: z.string(), value: z.string(), dim: z.string() }))
    .nullable(),
});
export type Health = z.infer<typeof Health>;

/** A rollup capped server-side, with `has_more` rather than a total. */
const capped = <T extends z.ZodType>(row: T) =>
  z.object({ rows: z.array(row), has_more: z.boolean() });

/** `GET /dashboard/api/corpus` (dashboard.md §24.1). */
export const Corpus = z.object({
  counted_at: epoch(),
  redacted: z.boolean(),
  corpus: z.object({
    videos: count(),
    /** Seconds: hours are a display rounding and are not on the wire. */
    duration_s: seconds(),
    cues: count(),
    keyframes: count(),
    ocr_lines: count(),
    chunks: count(),
    published: Published,
  }),
  /** Sums to `corpus.videos`. */
  videos_by_state: z.object({
    ready: count(),
    pending: count(),
    indexing: count(),
    failed: count(),
    stale: count(),
  }),
  channels: capped(z.object({ channel: z.string(), videos: count(), seconds: seconds() })),
  tags: capped(z.object({ tag: z.string(), videos: count() })),
  storage: Storage.nullable(),
});
export type Corpus = z.infer<typeof Corpus>;
