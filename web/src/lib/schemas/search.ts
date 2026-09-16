// `GET /api/search`'s payload, which both halves read: the public facade sends
// it to the demo, and `/dashboard/api/search` is that same handler behind the
// read gate (dashboard.md §14.2). One schema, so the two cannot drift.
import { z } from "zod";

// No `new Function` probe: the CSP has no 'unsafe-eval', and the probe alone
// logs a violation. These payloads are small enough not to need a compiler.
z.config({ jitless: true });

/** Every payload URL reaches an href or a src, so only http(s) passes. */
export const httpUrl = () => z.url().refine(isHttpUrl, "must be an http(s) URL");

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

/**
 * A page of results. One unreadable hit costs one hit, and a note says so;
 * everything outside `results` is strict (demo-site.md §2).
 */
export const SearchResponse = z
  .object({
    query: z.string(),
    content_type: ContentType,
    results: z.array(z.unknown()),
    pagination: Pagination,
    leg_counts: z.record(z.string(), z.number()).optional(),
    notes: z.array(z.string()),
    // Set on the empty path only: "nothing matched" vs "nothing is indexed".
    data_status: z.string().nullable(),
  })
  .transform((page) => {
    const results: Hit[] = [];
    let dropped = 0;
    for (const row of page.results) {
      const parsed = Hit.safeParse(row);
      if (parsed.success) results.push(parsed.data);
      else dropped += 1;
    }
    return {
      ...page,
      results,
      dropped,
      notes: dropped
        ? [
            ...page.notes,
            `${dropped} result(s) came back in a shape this page cannot read and were left out.`,
          ]
        : page.notes,
    };
  });
export type SearchResponse = z.infer<typeof SearchResponse>;
