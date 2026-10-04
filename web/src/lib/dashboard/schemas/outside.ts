import { z } from "zod";
import { count, epoch, httpUrl, seconds } from "./common";

// Discovery outside the follows (dashboard.md §27, companion.md §6.2).

export const OutsideFollow = z.object({
  state: z.enum(["none", "trial", "lasting", "ended"]),
  until: epoch().nullable(),
});
export type OutsideFollow = z.infer<typeof OutsideFollow>;

export const OutsideFeedback = z.enum(["none", "up", "down"]);
export type OutsideFeedback = z.infer<typeof OutsideFeedback>;

export const OutsidePick = z.object({
  id: count(),
  video_id: z.string(),
  url: httpUrl(),
  title: z.string(),
  channel: z.string().nullable(),
  channel_url: z.string().nullable(),
  duration_s: seconds(),
  published_at: epoch().nullable(),
  /** The profile entry it was scouted for. */
  because: z.string(),
  score: count().nullable(),
  reason: z.string().nullable(),
  summary: z.string().nullable(),
  moments: z.array(
    z.object({ offset_s: seconds(), end_s: seconds().nullable(), why: z.string(), url: httpUrl() }),
  ),
  feedback: OutsideFeedback,
  follow: OutsideFollow,
});
export type OutsidePick = z.infer<typeof OutsidePick>;

export const Speaker = z.object({
  id: count(),
  name: z.string(),
  reason: z.string(),
  state: z.enum(["open", "dismissed", "followed"]),
  talk: z.object({ video_id: z.string().nullable(), title: z.string() }),
  channel: z.object({ name: z.string().nullable(), url: z.string() }).nullable(),
  talks: z.array(
    z.object({
      video_id: z.string(),
      title: z.string(),
      channel: z.string().nullable(),
      url: httpUrl(),
    }),
  ),
  follow: OutsideFollow,
});
export type Speaker = z.infer<typeof Speaker>;

export const OutsideWeek = z.object({
  week: z.string(),
  picks: z.array(OutsidePick),
  speaker: Speaker.nullable(),
  /** Absent inside the brief, which carries the same week. */
  scouting: z.boolean().optional(),
});
export type OutsideWeek = z.infer<typeof OutsideWeek>;

export const OutsideFeedbackStored = z.object({
  id: count(),
  state: OutsideFeedback,
  offer: z.object({ channel: z.string().nullable(), url: z.string(), days: count() }).nullable(),
});
export type OutsideFeedbackStored = z.infer<typeof OutsideFeedbackStored>;

export const TrialStarted = z.object({
  url: z.string(),
  title: z.string(),
  trial_until: epoch().nullable(),
  already: z.boolean(),
});
export type TrialStarted = z.infer<typeof TrialStarted>;

export const SpeakerDismissed = z.object({ id: count(), state: z.literal("dismissed") });
