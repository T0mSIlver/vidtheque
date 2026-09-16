import { z } from "zod";
import { clockOf, count, epoch, httpUrl, Pagination, PartialRefusal, seconds } from "./common";

// The jobs table, one job, and the two writes on a job (dashboard.md §5.4,
// §21). Numbers only: every rendering is the page's (§23), bar `basis`, which
// says what a percentage is computed over and is policy text.

/** What a job contains, from its items. Optional for an older instance. */
export const JobContents = z.object({
  title: z.string().nullable(),
  more: count(),
  channel: z.string().nullable(),
  note: z.string().nullable(),
});
export type JobContents = z.infer<typeof JobContents>;

export const JobCard = z.object({
  job_id: z.string(),
  state: z.string(),
  kind: z.string(),
  priority: count(),
  /** A whole percent. */
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
  /** Which of the three is `null` is the fact: never claimed, never ran. */
  waited_s: count().nullable(),
  ran_s: count().nullable(),
  wall_s: count().nullable(),
  /** `queued|running`: the poll's stop condition. */
  live: z.boolean(),
  /** `not_before` still to run, and `0` on anything but a queued job. */
  defer_s: count(),
  error_code: z.string().nullable(),
  /** `null` in the projection (§2.4). */
  error_message: z.string().nullable(),
  /** `done` items with a failed stage underneath. */
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
  retries_left: count(),
  video_id: z.string().nullable(),
  title: z.string().nullable(),
  channel: z.string().nullable(),
  duration_s: seconds().nullable(),
  /** `null` in the projection. */
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
  /** `null` in the projection. */
  message: z.string().nullable(),
});
export type JobEvent = z.infer<typeof JobEvent>;

/** Whether the listing ran under the projection, so a `null` message can be
 *  told from a redacted one. Optional for an older instance. */
const redactedFlag = () => z.boolean().optional();

export const Jobs = z.object({
  redacted: redactedFlag(),
  now: epoch(),
  /** The server's cadence, floored again in the browser. */
  poll_ms: count(),
  live: z.boolean(),
  jobs: z.array(JobCard),
  pagination: Pagination,
  /** The predicates the listing ran with, not the URL's words. */
  filters: z.object({
    state: z.string(),
    kind: z.string(),
    error_code: z.string().nullable(),
    degraded: z.boolean(),
    order: z.string(),
  }),
  notes: z.array(z.string()),
});
export type Jobs = z.infer<typeof Jobs>;

/** One job. Six panels' fields are optional until the payload sends them. */
export const JobDetail = z.object({
  redacted: redactedFlag(),
  now: epoch(),
  poll_ms: count(),
  live: z.boolean(),
  job: JobCard,
  items: z.array(JobItem),
  events: z.array(JobEvent),
  items_capped: z.boolean().optional(),
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

/** `POST /dashboard/jobs/{id}/cancel`: the state the job is in now. */
export const CancelOutcome = z.object({
  job_id: z.string(),
  state: z.string(),
  cancel_requested: z.boolean(),
});
export type CancelOutcome = z.infer<typeof CancelOutcome>;

/** `POST /dashboard/jobs/{id}/retry`: every new job, always a list; `409`
 *  carries the same shape with the refusals in `errors`. */
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
