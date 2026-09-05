import type { Metadata } from "next";
import { OverviewView } from "./OverviewView";

// `GET /dashboard` — the corpus overview (dashboard.md §5.1), the first of the
// Jinja pages to be served from here.
//
// The page itself is a shell with no data in it: the reading happens in the
// browser, with the session cookie, against `/dashboard/api/overview`. So this
// component exists to do the one thing a Client Component cannot — name the
// document — and to render the view under it.
export const metadata: Metadata = { title: "Corpus overview" };

export default function DashboardOverviewPage() {
  return <OverviewView />;
}
