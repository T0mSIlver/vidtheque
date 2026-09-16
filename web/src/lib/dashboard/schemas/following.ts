import { z } from "zod";
import { clockOf, count, epoch, httpUrl, Pagination, seconds } from "./common";

// The following pages' reads and writes (dashboard.md §18, §21, §22). Both
// reads are registered with the write routes, so a `404` on them means "no
// write side here". The rule as facts, as a sentence and the near-miss line are
// composed by the pages; `reason` travels verbatim because it is the receipt.

/** One follow, as every payload describes it, built from `Rules.from_row`. */
export const FollowRow = z.object({
  slug: z.string(),
  title: z.string(),
  kind: z.string(),
  source_url: httpUrl(),
  state: z.string(),
  /** `failing` covers retrying and gave-up since 0008; the count is the
   *  receipt, not a fourth state. */
  fail_count: count(),
  retrying: z.boolean(),
  max_tries: count(),
  /** Policy text: the refusal a check-now would meet, `null` when it would
   *  be scheduled. */
  not_schedulable_reason: z.string().nullable(),
  mode: z.string(),
  tabs: z.array(z.string()),
  /** `all`, or a comma-joined subset. */
  channels: z.string(),
  tags: z.array(z.string()),
  min_duration_s: count().nullable(),
  max_duration_s: count().nullable(),
  title_include: z.array(z.string()),
  title_exclude: z.array(z.string()),
  backfill: count(),
  max_per_check: count(),
  check_interval_s: count(),
  /** `0` is due now, which is a value and not a missing clock. */
  next_check_at: clockOf(),
  last_check_at: clockOf(),
  last_new_at: clockOf(),
});
export type FollowRow = z.infer<typeof FollowRow>;

export const FollowListRow = FollowRow.extend({
  last_error_code: z.string().nullable(),
});
export type FollowListRow = z.infer<typeof FollowListRow>;

/** The detail's row, and every write outcome's: a resume clears both error
 *  columns, so the outcome has to carry them. */
export const FollowDetailRow = FollowListRow.extend({
  last_error_message: z.string().nullable(),
});
export type FollowDetailRow = z.infer<typeof FollowDetailRow>;

export const Following = z.object({
  counted_at: epoch(),
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
    spent_s: seconds(),
    /** `0` is the ceiling turned off, not "no budget left". */
    ceiling_h: z.number(),
    window_s: count(),
  }),
  /** With checks off every `next_check_at` is a time nothing happens at. */
  checks_enabled: z.boolean(),
  vectors: z.boolean(),
  vectors_reason: z.string().nullable().optional(),
  follows: z.array(FollowListRow),
  /** Candidates waiting on a person, capped apart from the pager. */
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
  pagination: Pagination,
  notes: z.array(z.string()),
});
export type Following = z.infer<typeof Following>;

/** A job this follow owns, as an id and a state; never a copy of the job. */
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

/** A candidate the follow passed over; `reason` is verbatim. */
export const SeenRow = z.object({
  title: z.string(),
  url: httpUrl(),
  decision: z.string(),
  reason: z.string().nullable(),
  judged_from: z.string(),
  duration_s: seconds().nullable(),
  published_at: clockOf(),
  decided_at: clockOf(),
});
export type SeenRow = z.infer<typeof SeenRow>;

export const FollowDetail = z.object({
  fetched_at: epoch(),
  follow: FollowDetailRow,
  checks_enabled: z.boolean(),
  brought_in: count(),
  counts: z.record(z.string(), count()),
  /** `null` means print nothing: a zero is not a finding. */
  near_miss: z
    .object({ count: count(), of: count(), within_s: count(), edge: z.string() })
    .nullable(),
  checks: z.array(FollowJob),
  index_jobs: z.array(FollowJob),
  in_flight: z.string().nullable(),
  order: z.string(),
  seen: z.array(SeenRow),
  caps: z.object({ checks: count(), index_jobs: count() }),
  pagination: Pagination,
  notes: z.array(z.string()),
});
export type FollowDetail = z.infer<typeof FollowDetail>;

/** `POST /dashboard/following`: `already_following` made nothing. */
export const FollowCreated = z.object({
  follow: FollowDetailRow.nullable(),
  already_following: z.boolean(),
});
export type FollowCreated = z.infer<typeof FollowCreated>;

/** `state`, `check` and `rules`: the row re-read after the write. */
export const FollowWritten = z.object({ follow: FollowDetailRow });
export type FollowWritten = z.infer<typeof FollowWritten>;

/** `delete`: the videos stay, and so does the budget already spent (0007). */
export const FollowDeleted = z.object({
  slug: z.string(),
  deleted: z.boolean(),
  videos_kept: count(),
  spent_s: seconds(),
});
export type FollowDeleted = z.infer<typeof FollowDeleted>;

/** `queue` ("Index anyway"): nothing asked for is nothing done. */
export const FollowQueued = z.object({
  slug: z.string(),
  url: httpUrl().nullable(),
  job_id: z.string().nullable(),
});
export type FollowQueued = z.infer<typeof FollowQueued>;
