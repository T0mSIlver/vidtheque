import { ROOT } from "@/lib/dashboard/client";

// Which `/dashboard` pages this app serves, in one list.
//
// The port ran page by page (frontend-migration.md §1d), so *every* link into
// this surface had to know which half it pointed at: a page this app serves is
// reached with `Link`, which swaps the React tree, and anything else is a plain
// anchor, because a client-side navigation to it would ask this app's router
// for a route that does not exist.
//
// Every page is here now, and the question is still the right one to ask:
// `/dashboard` holds the thirteen POSTs, `/dashboard/api/*` and `/dashboard/logout`,
// none of which is a page, and a link is a `GET` that has to land on one.
//
// It is asked by the rail, by the overview's arrivals, by the ledger's figures
// and by the videos table's own tags — so it is answered once, here.
const PAGES: string[] = [
  ROOT,
  `${ROOT}/ledger`,
  `${ROOT}/search`,
  `${ROOT}/videos`,
  `${ROOT}/jobs`,
  // The three `GET`-only entries. `POST /dashboard/following` is the add form's
  // route, `POST /dashboard/index` is the index form's, and `POST
  // /dashboard/login` is the one that mints the session cookie — one path, two
  // owners, split by method — and nothing in this file or in `proxy.ts` can say
  // so, because both are path-only. It is the *link* question these answer, and
  // a link is a `GET`.
  `${ROOT}/following`,
  `${ROOT}/index`,
  `${ROOT}/login`,
];

// The three ported pages with an id in them. None is a prefix, and one segment
// is the whole reason: `/dashboard/videos/{id}/reindex`,
// `/dashboard/jobs/{id}/cancel` and the five
// `/dashboard/following/{slug}/…` writes are POSTs Python owns, and they have
// three segments under their section rather than two.
const DETAIL = [
  new RegExp(`^${ROOT}/videos/[^/]+$`),
  new RegExp(`^${ROOT}/jobs/[^/]+$`),
  new RegExp(`^${ROOT}/following/[^/]+$`),
];

/** Does this app serve the page `href` points at? Query and fragment are not
 *  part of the question — `/dashboard/videos?index_state=failed` is the videos
 *  page with a filter on it. */
export function isPorted(href: string): boolean {
  return PAGES.includes(bare(href)) || DETAIL.some((pattern) => pattern.test(bare(href)));
}

/**
 * Which section of the rail a page belongs to — `_chrome(request, page)`'s
 * word, which every Jinja view declared for itself.
 *
 * It is a table and not a prefix test, because that is the difference the
 * declaration made: the rail marked `Videos` current on a video's own page
 * because `views.video` said `"videos"`, and marked *nothing* current on the
 * sign-in page, which is under `/dashboard` like everything else. A
 * `startsWith` reading gets both of those right by luck and gets the next
 * `/dashboard/<section>/…` route wrong silently — it would inherit a section
 * from a page it has nothing to do with.
 *
 * The refusal panel asks the same question: `error.html` offers "Every job this
 * index has run" under the jobs section and "Everything that is indexed"
 * everywhere else, and that is this word, not the URL it was read from.
 */
export type Section =
  "corpus" | "ledger" | "search" | "videos" | "jobs" | "index" | "following" | "login";

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

/** The section this path is in, or `null` for a path no page of this surface
 *  claims. */
export function sectionOf(href: string | null | undefined): Section | null {
  if (!href) return null;
  const path = bare(href);
  return SECTIONS.find(([pattern]) => pattern.test(path))?.[1] ?? null;
}

const bare = (href: string) => href.split("?")[0].split("#")[0];
