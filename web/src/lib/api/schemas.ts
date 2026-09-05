// The wire contract of the `/api/*` facade (docs/design/demo-site.md §2),
// declared once as Zod schemas. The TypeScript types are inferred from them,
// so there is no second declaration to drift, and `parse()` at the boundary
// turns a server-side change into a loud error here rather than an
// `undefined` three components deep.
//
// `z.object` strips unknown keys, which is the forward-compatible reading of
// the contract: an unknown field is ignored (DECISIONS.md, frame embeddings).
import { z } from "zod";

// Zod compiles a validator with `new Function` when it can, and finds out
// whether it can by calling `Function("")` the first time something parses.
// In the browser that call is refused — the page's `script-src` carries no
// `'unsafe-eval'` (see `proxy.ts`) — and the browser reports the refusal,
// which puts a CSP violation in the console for a feature probe that was
// always going to fall back. `jitless` makes the fallback the decision, on
// both sides of the boundary: these schemas parse a few small objects per
// request and one small event per stream frame, and never needed a compiler.
z.config({ jitless: true });

// Every URL in a payload is rendered: as an anchor's href, or as an image's
// src. `javascript:` and `data:` are URLs by the parser's reckoning and
// scripts by the browser's, and the facade has no reason to mint either, so
// the contract says http(s) and a payload that says otherwise is rejected
// here rather than handed to the DOM.
const httpUrl = () => z.url().refine(isHttpUrl, "must be an http(s) URL");

function isHttpUrl(value: string): boolean {
  try {
    const scheme = new URL(value).protocol;
    return scheme === "http:" || scheme === "https:";
  } catch {
    return false;
  }
}

export const Pagination = z.object({
  limit: z.number().int(),
  offset: z.number().int(),
  has_more: z.boolean(),
  // Never an exact total (tool-surface.md): a bounded estimate, or absent.
  approx_total: z.number().int().nullable().optional(),
  pool_exhausted: z.boolean().optional(),
});
export type Pagination = z.infer<typeof Pagination>;

// A search hit's `source` is one leg or a fusion of legs ("ocr+frame"), so it
// stays a string rather than an enum the server can outgrow.
export const Hit = z.object({
  source: z.string(),
  video_id: z.string(),
  title: z.string(),
  channel: z.string(),
  start: z.number(),
  end: z.number().nullable(),
  match_start: z.number().nullable(),
  match_cue_id: z.number().int().nullable(),
  text: z.string().nullable(),
  link: httpUrl(),
  cue_ids: z.array(z.number().int()),
  frame_id: z.string().nullable(),
  score: z.number(),
  timestamp: z.string(),
  // Both null when the hit has no frame: the page falls back to a text card.
  thumb: httpUrl().nullable(),
  thumb_large: httpUrl().nullable(),
});
export type Hit = z.infer<typeof Hit>;

export const ContentType = z.enum(["all", "transcript", "ocr", "frame"]);
export type ContentType = z.infer<typeof ContentType>;

export const SearchResponse = z.object({
  query: z.string(),
  content_type: ContentType,
  results: z.array(Hit),
  pagination: Pagination,
  leg_counts: z.record(z.string(), z.number()).optional(),
  // The same `note:` lines the MCP payload prints. "all means all" is a
  // promise to a human too, so the page renders them.
  notes: z.array(z.string()),
  // Set only on the empty path: "nothing matched" and "nothing is indexed" are
  // different screens.
  data_status: z.string().nullable(),
});
export type SearchResponse = z.infer<typeof SearchResponse>;

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

// One video, from `GET /api/videos/{id}` (demo-site.md §2.2.1): the
// video-summary tool's payload plus the frame URLs only the facade can mint.
export const Chapter = z.object({
  start: z.number(),
  title: z.string(),
  link: httpUrl(),
});
export type Chapter = z.infer<typeof Chapter>;

export const KeyText = z.object({
  start: z.number(),
  text: z.string().nullable(),
  link: httpUrl(),
});
export type KeyText = z.infer<typeof KeyText>;

export const OcrHighlight = z.object({
  t: z.number(),
  frame_id: z.string(),
  screen_text: z.string().nullable(),
  link: httpUrl(),
  thumb: httpUrl().nullable(),
  thumb_large: httpUrl().nullable(),
});
export type OcrHighlight = z.infer<typeof OcrHighlight>;

export const VideoDetail = z.object({
  video_id: z.string(),
  title: z.string(),
  channel: z.string(),
  published: z.string(),
  duration: z.string(),
  indexed_at: z.string(),
  link: httpUrl(),
  keyframes: z.number().int(),
  data_status: z.string(),
  tags: z.array(z.string()).optional(),
  chapters: z.array(Chapter).optional(),
  key_texts: z.array(KeyText).optional(),
  ocr_highlights: z.array(OcrHighlight).optional(),
  thumb: httpUrl().nullable(),
});
export type VideoDetail = z.infer<typeof VideoDetail>;

export const Meta = z.object({
  name: z.string(),
  version: z.string(),
  browse: z.string().nullable(),
  mcp_url: z.string(),
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

// What stopped an ask, whichever layer stopped it. §3.4's degraded body is
// one of them; the rate limiter's general envelope (§2.4) is another, and it
// has no `reason` to give, so requiring one there threw away the limiter's
// real sentence and its delay. A code and a sentence are what they all share.
export const AskFailure = z.object({
  error: z.string(),
  message: z.string(),
  // Present only on the typed degraded body — never invented for the rest.
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
  z.object({ event: z.literal("answer"), payload: AskAnswer }),
  z.object({ event: z.literal("error"), status: z.number().int(), payload: AskDegraded }),
]);
export type AskEvent = z.infer<typeof AskEvent>;

// Every non-2xx answer from the facade: a code, a sentence, and what to do
// next. The API serialises "no next step" as `next: null`, not by omitting the
// key, so a schema that only allowed a string rejected the whole envelope and
// lost the code and the sentence with it.
export const ErrorEnvelope = z.object({
  error: z.string(),
  message: z.string(),
  next: z.string().nullish(),
});
export type ErrorEnvelope = z.infer<typeof ErrorEnvelope>;

// The same envelope read field by field, so one field the API grew out from
// under us costs only that field. Used where an error body is the last thing
// we have to explain a failure with, and dropping it leaves nothing.
export const PartialErrorEnvelope = z.object({
  error: z.string().optional().catch(undefined),
  message: z.string().optional().catch(undefined),
  next: z.string().nullish().catch(undefined),
});
