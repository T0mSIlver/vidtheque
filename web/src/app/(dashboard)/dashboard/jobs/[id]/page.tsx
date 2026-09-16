import type { Metadata } from "next";
import { JobDetailView } from "./JobDetailView";

// The server never sees the session cookie, so it cannot name the job; the view
// renames the document when the read lands (dashboard.md §5.4).
export const metadata: Metadata = { title: "Job" };

export default async function DashboardJobPage({ params }: PageProps<"/dashboard/jobs/[id]">) {
  const { id } = await params;
  return <JobDetailView key={id} jobId={id} />;
}
