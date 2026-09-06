import type { Metadata } from "next";
import { OverviewView } from "./OverviewView";

// `GET /dashboard` — the corpus overview (dashboard.md §5.1), and the first
// page of this surface to be served from here rather than by Python.
//
// The page itself is a shell with no data in it: the reading happens in the
// browser, with the session cookie, against `/dashboard/api/overview`. So this
// component exists to do the one thing a Client Component cannot — name the
// document — and to render the view under it.
// `views.overview`'s own title, which is not the `<h1>`: the heading names the
// page and the document names the tab, and in a row of eight tabs "Corpus" is
// the word that tells them apart at 90px.
export const metadata: Metadata = { title: "Corpus" };

export default function DashboardOverviewPage() {
  return <OverviewView />;
}
