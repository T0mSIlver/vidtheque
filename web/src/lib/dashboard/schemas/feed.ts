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

/** A profile entry the verdict hit: `up` an entry you want more of, `down` one
 *  you want less of; strength 2 central to the video, 1 coming up (§25.2). */
export const Match = z.object({
  entry_id: count(),
  text: z.string(),
  direction: z.enum(["up", "down"]),
  strength: z.number().int().min(1).max(2),
});
export type Match = z.infer<typeof Match>;

export const FeedItem = FeedVideo.extend({
  /** 0 skip, 1 the summary is enough, 2 watch the moments, 3 watch it whole. */
  score: count(),
  /** What the feed shows: the week's ranking makes the 3s (companion.md §3.4). */
  tier: count().nullable().optional().default(null),
  week: z.string().nullable().optional().default(null),
  week_rank: count().nullable().optional().default(null),
  reason: z.string(),
  /** Scored 2+ only once rescored without the negative entries. */
  explored: z.boolean().optional().default(false),
  matches: z.array(Match).optional().default([]),
  /** Seconds the moments cover; null for moments written before spans. */
  moments_s: seconds().nullable().optional().default(null),
  judged_at: epoch(),
});
export type FeedItem = z.infer<typeof FeedItem>;

export const FeedOrder = z.enum(["newest", "oldest"]);
export type FeedOrder = z.infer<typeof FeedOrder>;

export const Feed = z.object({
  band: z.enum(["top", "skipped", "all"]),
  order: z.string(),
  q: z.string().nullable().optional(),
  channel: z.string().nullable().optional(),
  entry: z
    .union([count(), z.literal("other")])
    .nullable()
    .optional(),
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

/** A fitted row: what it asks of you, the whole video for a 3, its moments for a 2. */
export const WeekItem = FeedItem.extend({ asks_s: seconds() });
export type WeekItem = z.infer<typeof WeekItem>;

/** The week fitted to the owner's minutes (dashboard.md §25.13). */
export const Week = z.object({
  week: z.string(),
  previous: z.string(),
  next: z.string().nullable(),
  budget_min: count(),
  asks_s: seconds(),
  items: z.array(WeekItem),
  days: z.array(
    z.object({ day: z.string(), asks_s: seconds(), fitted: count(), candidates: count() }),
  ),
  rest: z.object({ count: count(), asks_s: seconds() }),
  capped: z.boolean(),
});
export type Week = z.infer<typeof Week>;

export const BudgetStored = z.object({ week_budget_min: count() });
export type BudgetStored = z.infer<typeof BudgetStored>;

/** What the feed's channel and entry filters offer for one band (§25.2). */
export const FeedFacets = z.object({
  band: z.enum(["top", "skipped", "all"]),
  channels: z.array(z.object({ name: z.string(), count: count() })),
  entries: z.array(
    z.object({
      entry_id: count(),
      text: z.string(),
      direction: z.enum(["up", "down"]),
      count: count(),
    }),
  ),
  other: count(),
  capped: z.boolean(),
});
export type FeedFacets = z.infer<typeof FeedFacets>;

/** The video a repeated stretch was first said in (§25.3). */
export const SeenVideo = z.object({
  video_id: z.string(),
  title: z.string(),
  channel: z.string().nullable(),
});
export type SeenVideo = z.infer<typeof SeenVideo>;

export const Moment = z.object({
  cue_id: count(),
  offset_s: seconds(),
  /** Where the moment ends: its last cue's end; null before spans. */
  end_s: seconds().nullable().optional().default(null),
  why: z.string(),
  /** Where `url` starts: past a repeat that covers the moment's start (#171). */
  start_s: seconds().optional(),
  url: httpUrl(),
  /** The first repeat the moment touches; `whole` when it repeats all through. */
  repeat: SeenVideo.extend({ whole: z.boolean() }).nullable().optional().default(null),
});
export type Moment = z.infer<typeof Moment>;

/** A stretch of this video that says what a video you saw said (§25.3). */
export const Overlap = z.object({
  video: SeenVideo,
  start_s: seconds(),
  end_s: seconds(),
  seen_s: seconds(),
  url: httpUrl(),
});
export type Overlap = z.infer<typeof Overlap>;

/** A video's one thumb-or-mute state; `none` once taken back (§25.4). */
export const FeedbackState = z.enum(["none", "up", "down", "muted"]);
export type FeedbackState = z.infer<typeof FeedbackState>;

export const Verdict = z.object({
  video: FeedVideo,
  score: count(),
  tier: count().nullable().optional().default(null),
  week: z.string().nullable().optional().default(null),
  week_rank: count().nullable().optional().default(null),
  reason: z.string(),
  explored: z.boolean().optional().default(false),
  matches: z.array(Match).optional().default([]),
  feedback: FeedbackState.optional().default("none"),
  summary: z.string(),
  moments: z.array(Moment),
  overlaps: z.array(Overlap).optional().default([]),
  moments_dropped: count(),
  moments_s: seconds().nullable().optional().default(null),
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
  // `mcp_search` is the search page's query, which names no video (§25.4).
  kind: z.union([SignalKind, z.literal("mcp_search")]),
  video_id: z.string().nullable(),
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
  /** A project lapses at `expires_at` unless written again (#159). */
  kind: z.enum(["topic", "project"]).default("topic"),
  expires_at: epoch().nullable().default(null),
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

/** Each wanted entry's moments: how many, and their minutes (§25.14). */
export const Collections = z.object({
  collections: z.array(
    z.object({
      entry_id: count(),
      text: z.string(),
      moments: count(),
      moments_s: seconds(),
      videos: count(),
      has_more: z.boolean(),
    }),
  ),
});
export type Collections = z.infer<typeof Collections>;

/** One entry's moments, best first; `repeat.item` is an earlier one's index. */
export const CollectionMoment = z.object({
  video: FeedVideo,
  why: z.string(),
  offset_s: seconds(),
  end_s: seconds(),
  start_s: seconds(),
  url: httpUrl(),
  repeat: z
    .object({ item: count(), video_id: z.string(), title: z.string(), whole: z.boolean() })
    .nullable(),
});
export type CollectionMoment = z.infer<typeof CollectionMoment>;

export const Collection = z.object({
  entry: z.object({ entry_id: count(), text: z.string() }),
  moments: z.array(CollectionMoment),
  moments_s: seconds(),
  has_more: z.boolean(),
});
export type Collection = z.infer<typeof Collection>;
