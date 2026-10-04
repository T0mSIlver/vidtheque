import { z } from "zod";
import { count, Readiness } from "./common";

// `GET /dashboard/api/search` is the public facade's search handler behind the
// read gate (dashboard.md §14.2), so it answers in the facade's shape rather
// than this surface's. The schema is the shared one; this file is where the
// dashboard names it, so no page here reaches into `lib/api`.
export { ContentType, Hit, SearchResponse } from "@/lib/schemas/search";
export { badges, type Badge } from "@/lib/schemas/evidence";

/** `GET /dashboard/api/readiness`: Health's readiness block alone, asked
 *  while a search is slow (§28.1). */
export const ReadinessRead = z.object({ readiness: Readiness });
export type ReadinessRead = z.infer<typeof ReadinessRead>;

/** `GET /dashboard/api/channels`: every channel name, most videos first,
 *  capped with `has_more` (§28.1). */
export const Channels = z.object({
  channels: z.object({
    rows: z.array(z.object({ channel: z.string(), videos: count() })),
    has_more: z.boolean(),
  }),
});
export type Channels = z.infer<typeof Channels>;
