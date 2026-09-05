import { ROOT } from "@/lib/dashboard/client";

// Which `/dashboard` pages this app serves, in one list.
//
// The port is page by page (frontend-migration.md §1d), so on any given day
// half this surface is React and half is still Jinja, and *every* link into it
// has to know which half it is pointing at: a page this app serves is reached
// with `Link`, which swaps the React tree, and a page Python still renders is a
// plain anchor, because a client-side navigation to it would ask this app's
// router for a route that does not exist.
//
// That question is asked by the rail, by the overview's arrivals, by the
// ledger's figures and by the videos table's own tags — so it is answered
// once, here. Porting a page adds its path to this list, names it in
// `proxy.ts`'s matcher, and changes nothing else.
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
  const path = href.split("?")[0].split("#")[0];
  return PAGES.includes(path) || DETAIL.some((pattern) => pattern.test(path));
}
