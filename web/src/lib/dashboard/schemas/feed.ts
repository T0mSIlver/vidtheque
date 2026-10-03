import { z } from "zod";
import { count, epoch, httpUrl, seconds } from "./common";

// The feed's reads and writes (dashboard.md §25, companion.md §6). JSON in and
// out; the pages under `/feed` and the Android app read the same payloads.

/** The video a verdict is about, as both reads describe it. */
export const FeedVideo = z.object({
  video_id: z.string(),
  title: z.string(),
  channel: z.string().nullable(),
  duration_s: seconds(),
  published_at: epoch().nullable(),
});
export type FeedVideo = z.infer<typeof FeedVideo>;

export const FeedItem = FeedVideo.extend({
  /** 0 skip, 1 the summary is enough, 2 watch the moments, 3 watch it whole. */
  score: count(),
  reason: z.string(),
  /** Scored 2+ only once rescored without the negative entries. */
  explored: z.boolean().optional().default(false),
  judged_at: epoch(),
});
export type FeedItem = z.infer<typeof FeedItem>;

export const Feed = z.object({
  band: z.enum(["top", "skipped"]),
  order: z.string(),
  items: z.array(FeedItem),
  pagination: z.object({
    limit: count(),
    offset: count(),
    has_more: z.boolean(),
    next_offset: count().nullable(),
  }),
  skipped: z.object({ count: count(), capped: z.boolean() }),
});
export type Feed = z.infer<typeof Feed>;

export const Moment = z.object({
  cue_id: count(),
  offset_s: seconds(),
  why: z.string(),
  url: httpUrl(),
});
export type Moment = z.infer<typeof Moment>;

/** A video's one thumb-or-mute state; `none` once taken back (§25.4). */
export const FeedbackState = z.enum(["none", "up", "down", "muted"]);
export type FeedbackState = z.infer<typeof FeedbackState>;

export const Verdict = z.object({
  video: FeedVideo,
  score: count(),
  reason: z.string(),
  explored: z.boolean().optional().default(false),
  feedback: FeedbackState.optional().default("none"),
  summary: z.string(),
  moments: z.array(Moment),
  moments_dropped: count(),
  profile_rev: count(),
  model: z.string().nullable(),
  judged_at: epoch(),
});
export type Verdict = z.infer<typeof Verdict>;

export const SignalKind = z.enum([
  "open",
  "watch",
  "ask_claude",
  "thumb_up",
  "thumb_down",
  "mute",
  "dismiss",
]);
export type SignalKind = z.infer<typeof SignalKind>;

export const SignalRecorded = z.object({
  recorded: z.boolean(),
  signal_id: count(),
  kind: SignalKind,
  video_id: z.string(),
});
export type SignalRecorded = z.infer<typeof SignalRecorded>;

export const FeedbackStored = z.object({ video_id: z.string(), state: FeedbackState });
export type FeedbackStored = z.infer<typeof FeedbackStored>;

export const ProfileEntry = z.object({
  id: count(),
  text: z.string(),
  /** In [-1, 1]; negative is "less of this". */
  weight: z.number(),
  source: z.string(),
  created_at: epoch(),
  evidence: z.string().nullable(),
});
export type ProfileEntry = z.infer<typeof ProfileEntry>;

/** An entry's state on either side of an event (`profile/store.py`). */
const EntryState = z
  .object({
    text: z.string().optional(),
    weight: z.number().optional(),
    live: z.boolean().optional(),
  })
  .nullable();

export const ProfileEvent = z.object({
  id: count(),
  at: epoch(),
  actor: z.string(),
  op: z.string(),
  entry_id: count(),
  before: EntryState,
  after: EntryState,
  reason: z.string().nullable(),
});
export type ProfileEvent = z.infer<typeof ProfileEvent>;

export const Profile = z.object({
  revision: count(),
  max_entries: count(),
  entries: z.array(ProfileEntry),
  history: z.object({
    events: z.array(ProfileEvent),
    limit: count(),
    has_more: z.boolean(),
    next_before: count().nullable(),
  }),
});
export type Profile = z.infer<typeof Profile>;

export const ProfileApplied = Profile.extend({
  applied: z.object({ events: z.array(count()), duplicates: z.array(z.string()) }),
});
export type ProfileApplied = z.infer<typeof ProfileApplied>;

export const ProfileReverted = Profile.extend({
  reverted: z.object({ events: z.array(count()) }),
});
export type ProfileReverted = z.infer<typeof ProfileReverted>;

/** What `POST /dashboard/api/profile` takes. */
export interface ProfileOps {
  add?: { text: string; weight: number }[];
  drop?: number[];
  reweight?: { id: number; weight: number }[];
  reason?: string;
}
