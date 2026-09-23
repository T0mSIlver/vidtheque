import { z } from "zod";
import { clockOf, count, epoch, httpUrl, PartialRefusal, seconds, ToolError } from "./common";

// The videos table, one video's panels and its cues (dashboard.md §20), and
// the three writes on the corpus (§21). `library` rather than `videos`:
// `/dashboard/api/videos` is the public facade's listing.

export const LibraryRow = z.object({
  video_id: z.string(),
  title: z.string(),
  channel: z.string(),
  published_at: clockOf(),
  duration_s: seconds().nullable(),
  indexed_at: clockOf(),
  index_state: z.string(),
  coverage: z.object({ transcript: z.boolean(), ocr: z.boolean(), frames: z.boolean() }),
  tags: z.array(z.string()),
  thumb: httpUrl().nullable(),
  link: httpUrl(),
});
export type LibraryRow = z.infer<typeof LibraryRow>;

/** The filters the query ran with, resolved: on the answer and on its refusal.
 *  Dates are clamped and snapped to a UTC day; `_before` is exclusive. */
export const LibraryFilters = z.object({
  q: z.string().nullable(),
  channel: z.string().nullable(),
  tags: z.array(z.string()),
  has: z.string(),
  index_state: z.string(),
  published_after: clockOf(),
  published_before: clockOf(),
  indexed_after: clockOf(),
  indexed_before: clockOf(),
});
export type LibraryFilters = z.infer<typeof LibraryFilters>;

export const Library = z.object({
  counted_at: epoch(),
  redacted: z.boolean(),
  /** Explicit, never inferred from `q`. */
  order: z.string(),
  filters: LibraryFilters,
  videos: z.array(LibraryRow),
  pagination: z.object({
    limit: count(),
    offset: count(),
    has_more: z.boolean(),
    /** Only past the end: where the last page starts. */
    last_offset: count().optional(),
  }),
  /** Exact, not the tool's `~` probe (§5.2). */
  total: count(),
  notes: z.array(z.string()),
});
export type Library = z.infer<typeof Library>;

/** A refused table read still echoes what it resolved (§20). */
export const RefusedLibrary = PartialRefusal.extend({ filters: LibraryFilters.optional() });
export type RefusedLibrary = z.infer<typeof RefusedLibrary>;

/** One of the seven `video_stages` rows; `absent` never ran. `model_key` and
 *  `error` are `null` in the projection. */
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

/** A line read off a keyframe, with its box normalised 0–1. */
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
  dup_of_ord: count().nullable(),
  // Three widths by URL, never inline base64.
  thumb: httpUrl(),
  detail: httpUrl(),
  large: httpUrl(),
  lines: z.array(OcrLine),
});
export type FrameCard = z.infer<typeof FrameCard>;

/** The cut structure as seconds; percentages are the page's rendering. */
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
  /** `video-summary`'s word, or `null` when it refused (`summary_error`). */
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
  cue_origins: z.record(z.string(), count()),
  /** A pointer to the cues endpoint, not a copy (§20). */
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
    // The page's line budget, and whether it ran out (§5.3's double cap).
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

/** `GET /dashboard/api/videos/{id}/cues`, in numbers; `t` is the whole second a
 *  `?t=` deeplink takes. */
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
  in_chunk: z.boolean(),
});
export type Cue = z.infer<typeof Cue>;

export const CuePage = z.object({
  cues: z.array(Cue),
  offset: count(),
  limit: count(),
  has_more: z.boolean(),
});
export type CuePage = z.infer<typeof CuePage>;

/** What `_submitted` resolved: the batch's real `expand`, `max_items` and
 *  `priority`. */
const IndexAccepted = z.object({
  expand: z.string(),
  max_items: count(),
  priority: z.string(),
});
type IndexAccepted = z.infer<typeof IndexAccepted>;

/** `POST /dashboard/index`: a batch split server-side into jobs (§10.7). `409`
 *  carries the same receipt with every refusal in `errors`. */
export const IndexOutcome = z.object({
  jobs: z.array(z.object({ job_id: z.string(), items: count(), urls: z.array(z.string()) })),
  already_indexed: z.array(z.string()),
  errors: z.array(PartialRefusal.extend({ urls: z.array(z.string()) })),
  batches: count(),
  urls: count(),
  accepted: IndexAccepted.optional(),
});
export type IndexOutcome = z.infer<typeof IndexOutcome>;

/** The form's two refusals still echo what `_submitted` resolved (§21). */
export const RefusedIndex = PartialRefusal.extend({ accepted: IndexAccepted.optional() });
export type RefusedIndex = z.infer<typeof RefusedIndex>;

/** `POST /dashboard/videos/{id}/reindex`: forced, one job. */
export const ReindexOutcome = z.object({
  video_id: z.string(),
  job_id: z.string().nullable(),
});
export type ReindexOutcome = z.infer<typeof ReindexOutcome>;

/** `POST /dashboard/videos/{id}/tags`: the row's tags after the write. */
export const TagsOutcome = z.object({
  video_id: z.string(),
  tags: z.array(z.string()),
});
export type TagsOutcome = z.infer<typeof TagsOutcome>;
