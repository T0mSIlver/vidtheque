import { ROOT } from "@/lib/dashboard/client";

// Which `/dashboard` paths are pages this app serves, and which rail section
// each belongs to. Everything else under the prefix (`/api/*`, `/logout`, the
// POSTs) is Python's and is reached by a plain anchor (dashboard.md §2).

export type Section =
  "corpus" | "ledger" | "search" | "videos" | "jobs" | "index" | "following" | "login";

// A table rather than a prefix test: a detail page declares its section, and
// the sign-in page is in none of the rail's.
const SECTIONS: [RegExp, Section][] = [
  [new RegExp(`^${ROOT}$`), "corpus"],
  [new RegExp(`^${ROOT}/ledger$`), "ledger"],
  [new RegExp(`^${ROOT}/search$`), "search"],
  [new RegExp(`^${ROOT}/videos(?:/[^/]+)?$`), "videos"],
  [new RegExp(`^${ROOT}/jobs(?:/[^/]+)?$`), "jobs"],
  [new RegExp(`^${ROOT}/index$`), "index"],
  [new RegExp(`^${ROOT}/following(?:/[^/]+)?$`), "following"],
  [new RegExp(`^${ROOT}/login$`), "login"],
];

/** The section this path is in, or `null` for a path no page claims. Query
 *  and fragment are not part of the question. */
export function sectionOf(href: string | null | undefined): Section | null {
  if (!href) return null;
  const path = href.split("?")[0].split("#")[0];
  return SECTIONS.find(([pattern]) => pattern.test(path))?.[1] ?? null;
}

/** Does this app serve the page `href` points at? */
export function isPorted(href: string): boolean {
  return sectionOf(href) !== null;
}
