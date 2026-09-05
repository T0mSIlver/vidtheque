import type { Metadata } from "next";
import { JobsView } from "./JobsView";

// `GET /dashboard/jobs` — the triage table and its 2 s tick (dashboard.md
// §5.4, §16.3). A shell with no data in it: the reading is the browser's,
// against `/dashboard/api/jobs` with the session cookie.
export const metadata: Metadata = { title: "Jobs" };

export default function DashboardJobsPage() {
  return <JobsView />;
}
