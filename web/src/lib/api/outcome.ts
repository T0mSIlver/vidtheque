// The ways a search read ends, shared by the server's first page and the
// browser's later ones. A failed read is a state the console draws in the
// facade's own words, never a thrown render (demo-site.md §6.1).
import type { SearchResponse } from "./schemas";

export const SEARCH_PAGE = 10;

/** The limiter's own minute, for a 429 that did not say how long. */
export const RETRY_FALLBACK = 60;

/** The server refuses a longer question (demo-site.md §3), so the field stops
 *  at the same number rather than letting one be typed and then refused. */
export const ASK_MAX_CHARS = 1000;

export type SearchOutcome =
  | { kind: "ok"; page: SearchResponse }
  | { kind: "rate_limited"; retryAfter: number }
  | { kind: "refused"; message: string; next?: string }
  | { kind: "unreachable" };
