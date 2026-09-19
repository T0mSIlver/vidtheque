// The `/api/*` wire contract (demo-site.md §2) as Zod schemas; types are
// inferred. Unknown keys are stripped: an added field is ignored.
import { z } from "zod";
// The search payload is the one shape the dashboard reads too, so it lives in
// `lib/schemas` and is re-exported here; nothing else on this page is shared.
import { httpUrl, Pagination } from "@/lib/schemas/search";

export { ContentType, Hit, Pagination, SearchResponse } from "@/lib/schemas/search";

// `mcp_url` is pasted into a shell (`claude mcp add … <mcp_url>`), so shell
// punctuation, whitespace included, makes it not an endpoint.
const SHELL_PUNCTUATION = /[\s;&|<>$`'"\\()]/;
const pasteableUrl = () =>
  httpUrl().refine((value) => !SHELL_PUNCTUATION.test(value), "must be a plain http(s) URL");

export const Video = z.object({
  video_id: z.string(),
  title: z.string(),
  channel: z.string(),
  published: z.string(),
  duration: z.string(),
  coverage: z.string(),
  tags: z.string(),
  indexed_at: z.string(),
  index_state: z.string(),
  link: httpUrl(),
  thumb: httpUrl().nullable(),
});
export type Video = z.infer<typeof Video>;

export const VideosResponse = z.object({
  videos: z.array(Video),
  pagination: Pagination,
});
export type VideosResponse = z.infer<typeof VideosResponse>;

const EditionSpeaker = z.object({ name: z.string(), company: z.string() });
const EditionAlignment = z.object({
  talk_video_id: z.string().nullable(),
  stream_video_id: z.string().nullable(),
  start_s: z.number().nullable(),
  end_s: z.number().nullable(),
});
export const EditionSession = z.object({
  id: z.string(),
  day: z.string(),
  start: z.string(),
  end: z.string(),
  stage: z.string(),
  title: z.string(),
  speakers: z.array(EditionSpeaker),
  category: z.string(),
  alignment: EditionAlignment.nullable(),
});
export type EditionSession = z.infer<typeof EditionSession>;

export const EditionTalk = z.object({
  session_id: z.string(),
  day: z.string(),
  scheduled_start: z.string(),
  scheduled_end: z.string(),
  title: z.string(),
  speakers: z.array(EditionSpeaker),
  category: z.string(),
  alignment_state: z.enum(["not_yet_indexed", "indexed_not_aligned", "aligned"]),
  video_id: z.string().nullable(),
  source_kind: z.enum(["talk", "stream"]).nullable(),
  start_s: z.number().nullable(),
  end_s: z.number().nullable(),
  source: httpUrl().nullable(),
});
export type EditionTalk = z.infer<typeof EditionTalk>;

const EditionPage = z.object({
  limit: z.number().int(),
  offset: z.number().int(),
  has_more: z.boolean(),
  next_offset: z.number().int().nullable(),
});

export const EditionResponse = z.object({
  edition: z.object({
    schema_version: z.literal(1),
    slug: z.string(),
    title: z.string(),
    timezone: z.string(),
    starts_on: z.string(),
    ends_on: z.string(),
    source_url: httpUrl(),
    source_captured_on: z.string(),
    organizer: z.string(),
    streamed_stage: z.string(),
    tags: z.object({ edition: z.string(), stream: z.string(), talk: z.string() }),
  }),
  sessions: z.array(EditionSession),
  pagination: EditionPage,
  talks: z.array(EditionTalk),
  videos: z.array(
    z.object({
      video_id: z.string(),
      title: z.string(),
      channel: z.string(),
      duration: z.number(),
      tags: z.array(z.string()),
      index_state: z.string(),
      kind: z.enum(["talk", "stream", "other"]),
      link: httpUrl(),
    }),
  ),
  video_pagination: EditionPage,
  notes: z.array(z.string()),
});
export type EditionResponse = z.infer<typeof EditionResponse>;

export const Meta = z.object({
  name: z.string(),
  version: z.string(),
  browse: z.string().nullable(),
  mcp_url: pasteableUrl(),
  auth: z.string(),
  ask_enabled: z.boolean(),
  ask_model: z.string().nullable(),
  videos: z.number().int(),
  clamps: z.object({
    policy: z.string(),
    search_max_limit: z.number().int(),
    videos_max_limit: z.number().int(),
  }),
  limits: z.object({
    search_per_min: z.number().int(),
    ask_per_min: z.number().int(),
    ask_per_day: z.number().int(),
  }),
  repo: z.string(),
});
export type Meta = z.infer<typeof Meta>;

// `POST /api/ask` (demo-site.md §3, §3.5). The answer arrives whole, once;
// what streams before it is the work, one event per tool call.
export const Citation = z.object({
  n: z.number().int(),
  video_id: z.string(),
  title: z.string(),
  channel: z.string(),
  t: z.number(),
  timestamp: z.string(),
  link: httpUrl().nullable(),
  thumb: httpUrl().nullable(),
  thumb_large: httpUrl().nullable(),
  source: z.string().nullable(),
  text: z.string().nullable(),
  // What the model read of this moment, line by line (demo-site.md §3).
  read: z
    .array(z.object({ t: z.number(), text: z.string() }))
    .nullable()
    .optional(),
});
export type Citation = z.infer<typeof Citation>;

export const AskAnswer = z.object({
  answer: z.string(),
  citations: z.array(Citation),
  rounds: z.number().int().optional(),
  model: z.string().nullable(),
});
export type AskAnswer = z.infer<typeof AskAnswer>;

// The §3.4 body: the same shape as a 503's JSON and a stream's error event.
export const AskDegraded = z.object({
  error: z.string(),
  reason: z.string(),
  message: z.string(),
  retry_after_s: z.number().nullable(),
});
export type AskDegraded = z.infer<typeof AskDegraded>;

// What stopped an ask, whichever layer did: §3.4's degraded body or the
// limiter's envelope, which has no `reason`.
export const AskFailure = z.object({
  error: z.string(),
  message: z.string(),
  reason: z.string().nullish(),
  retry_after_s: z.number().nullish(),
});
export type AskFailure = z.infer<typeof AskFailure>;

export const AskEvent = z.discriminatedUnion("event", [
  z.object({
    event: z.literal("activity"),
    id: z.number().int(),
    phase: z.enum(["start", "done"]),
    text: z.string().optional(),
    result: z.string().optional(),
  }),
  // `took_s` is the server's clock: a replayed run lands at once (§3.6).
  z.object({ event: z.literal("answer"), payload: AskAnswer, took_s: z.number().optional() }),
  z.object({ event: z.literal("error"), status: z.number().int(), payload: AskDegraded }),
]);
export type AskEvent = z.infer<typeof AskEvent>;

// Every non-2xx answer: a code, a sentence, and what to do next (`null` for
// none).
export const ErrorEnvelope = z.object({
  error: z.string(),
  message: z.string(),
  next: z.string().nullish(),
  // Believed over `Retry-After`, which a proxy may rewrite.
  retry_after_s: z.number().nullish(),
});
export type ErrorEnvelope = z.infer<typeof ErrorEnvelope>;

// The envelope read field by field, so one unexpected field costs only itself.
export const PartialErrorEnvelope = z.object({
  error: z.string().optional().catch(undefined),
  message: z.string().optional().catch(undefined),
  next: z.string().nullish().catch(undefined),
  retry_after_s: z.number().nullish().catch(undefined),
});
