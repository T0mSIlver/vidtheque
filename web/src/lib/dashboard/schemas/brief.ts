import { z } from "zod";
import { count, epoch, httpUrl, seconds } from "./common";
import { FeedbackState, Match, Moment } from "./feed";
import { OutsideWeek } from "./outside";
import { ValuedTime } from "./valued-time";

// The weekly brief (dashboard.md §26, companion.md §6.1).

const BriefVideo = z.object({
  video_id: z.string(),
  title: z.string(),
  channel: z.string().nullable(),
  duration_s: seconds(),
  published_at: epoch().nullable(),
  score: count(),
  reason: z.string(),
});

const Receipt = z.object({
  video_id: z.string(),
  title: z.string(),
  channel: z.string(),
  cue_id: count(),
  offset_s: seconds(),
  url: httpUrl().nullable(),
});

const EntryState = z
  .object({
    text: z.string().optional(),
    weight: z.number().optional(),
    live: z.boolean().optional(),
  })
  .nullable();

export const Brief = z.object({
  /** The Monday the week starts, YYYY-MM-DD. */
  week: z.string(),
  since: epoch(),
  until: epoch(),
  built_at: epoch(),
  previous_week: z.string().nullable(),
  picks: z.array(BriefVideo.extend({ moments: z.array(Moment) })),
  said: z.array(
    z.object({
      entry_id: count(),
      text: z.string(),
      points: z.array(Receipt.extend({ said: z.string() })),
      disagreement: z.object({ about: z.string(), sides: z.array(Receipt) }).nullable(),
    }),
  ),
  said_note: z.string().nullable(),
  channels: z.array(
    z.object({
      slug: z.string(),
      title: z.string(),
      state: z.string(),
      videos: count(),
      judged: count(),
      worth_share: z.number().nullable(),
      engaged_share: z.number().nullable(),
      suggest_pause: z.boolean(),
    }),
  ),
  profile_changes: z.array(
    z.object({
      event_id: count(),
      at: epoch(),
      op: z.string(),
      entry_id: count(),
      before: EntryState,
      after: EntryState,
      reason: z.string().nullable(),
      reverted: z.boolean(),
    }),
  ),
  audit: z.array(
    BriefVideo.extend({ sunk_by: Match.nullable(), answer: z.enum(["right", "wrong"]).nullable() }),
  ),
  checkin: z.object({ rating: count(), missing: z.string().nullable(), at: epoch() }).nullable(),
  /** This week of the valued-time ledger (§25.12); null past the weeks it reads. */
  ledger: ValuedTime.nullable(),
  /** Discovery's week (§27.7); absent from a server before it. */
  outside: OutsideWeek.nullable().optional().default(null),
});
export type Brief = z.infer<typeof Brief>;

export const CheckinStored = z.object({
  week: z.string(),
  rating: count(),
  missing: z.string().nullable(),
});
export type CheckinStored = z.infer<typeof CheckinStored>;

export const SkipAnswer = z.enum(["right", "wrong"]);
export type SkipAnswer = z.infer<typeof SkipAnswer>;

/** "Lower the entry that sank it to `to`?", applied only by the owner. */
export const Proposal = z.object({
  entry_id: count(),
  text: z.string(),
  weight: z.number(),
  to: z.number(),
});
export type Proposal = z.infer<typeof Proposal>;

export const SkipAnswered = z.object({
  video_id: z.string(),
  answer: SkipAnswer,
  feedback: FeedbackState,
  proposal: Proposal.nullable(),
});
export type SkipAnswered = z.infer<typeof SkipAnswered>;
