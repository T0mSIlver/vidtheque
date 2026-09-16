import type { Metadata } from "next";
import { JobsView } from "./JobsView";

// A data-free shell; the browser reads `/dashboard/api/jobs` (dashboard.md §5.4).
export const metadata: Metadata = { title: "Jobs" };

export default function DashboardJobsPage() {
  return <JobsView />;
}
