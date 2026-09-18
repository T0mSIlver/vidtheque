// What the public chrome derives from `/api/meta` (demo-site.md §2.3). Pure;
// the read itself is `readMeta` in `lib/search.ts`.
import type { Meta } from "./schemas";

// Rate-limited and unreachable are different facts, and the page says which
// (`/api/meta` shares the search bucket; docs/LESSONS.md).
export type MetaOutcome =
  { kind: "ok"; meta: Meta } | { kind: "rate_limited" } | { kind: "unreachable" };

/** The masthead line: a fact about the corpus, not about a search. */
export function corpusCount(videos: number | null | undefined): string | null {
  if (!videos) return null;
  return `${videos} talk${videos === 1 ? "" : "s"} watched`;
}

/** `browse` as a same-origin path, or nothing: one leading slash (never
 *  `//host`) and route-prefix characters only (demo-site.md §2.3). */
export function browsePath(browse: string | null | undefined): string | null {
  if (typeof browse !== "string") return null;
  return /^\/[a-z0-9][a-z0-9/_-]*$/i.test(browse) ? browse : null;
}
