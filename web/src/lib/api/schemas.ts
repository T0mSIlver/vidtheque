// The wire contract of the `/api/*` facade (docs/design/demo-site.md §2),
// declared once as Zod schemas. The TypeScript types are inferred from them,
// so there is no second declaration to drift, and `parse()` at the boundary
// turns a server-side change into a loud error here rather than an
// `undefined` three components deep.
//
// `z.object` strips unknown keys, which is the forward-compatible reading of
// the contract: an unknown field is ignored (DECISIONS.md, frame embeddings).
import { z } from "zod";

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
  text: z.string(),
  link: z.url(),
  cue_ids: z.array(z.number().int()),
  frame_id: z.string().nullable(),
  score: z.number(),
  timestamp: z.string(),
  // Both null when the hit has no frame: the page falls back to a text card.
  thumb: z.url().nullable(),
  thumb_large: z.url().nullable(),
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
  link: z.url(),
  thumb: z.url().nullable(),
});
export type Video = z.infer<typeof Video>;

export const VideosResponse = z.object({
  videos: z.array(Video),
  pagination: Pagination,
});
export type VideosResponse = z.infer<typeof VideosResponse>;

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

// Every non-2xx answer from the facade: a code, a sentence, and what to do next.
export const ErrorEnvelope = z.object({
  error: z.string(),
  message: z.string(),
  next: z.string().optional(),
});
export type ErrorEnvelope = z.infer<typeof ErrorEnvelope>;
