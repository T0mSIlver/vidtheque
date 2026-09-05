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
const PAGES: string[] = [ROOT, `${ROOT}/ledger`, `${ROOT}/videos`];

/** Does this app serve the page `href` points at? Query and fragment are not
 *  part of the question — `/dashboard/videos?index_state=failed` is the videos
 *  page with a filter on it. */
export function isPorted(href: string): boolean {
  const path = href.split("?")[0].split("#")[0];
  return PAGES.includes(path);
}
