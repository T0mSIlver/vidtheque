import { notFound } from "next/navigation";

// Every path under `/dashboard` that is not one of the pages beside this file.
//
// It exists for one reason: `not-found.tsx` in a segment is rendered when
// `notFound()` is thrown *inside* that segment, and an unmatched URL is not
// inside anything — Next hands it the **root** `app/not-found.tsx`, which is
// the front door's page and knows nothing of this surface. So the segment
// catches the path itself and refuses it, and the refusal renders under this
// segment's layout: the rail, the skip link, the deployment state, and
// `dashboard/not-found.tsx` in the middle of them.
//
// Nothing is shadowed by this. A catch-all is the last thing the router tries,
// after every static and dynamic segment; and what Python still owns under this
// prefix — `/dashboard/api/*`, `/dashboard/logout`, the thirteen POSTs — never
// reaches the router at all (`next.config.ts` in development, the reverse proxy
// in production).
export default function DashboardCatchAll() {
  notFound();
}
