import type { Metadata } from "next";
import { JobDetailView } from "./JobDetailView";

// `GET /dashboard/jobs/{job_id}` — one job's war story, reading
// `/dashboard/api/jobs/{job_id}` in the browser (dashboard.md §5.4).
//
// The title here is the generic one and stays that way: this shell is rendered
// without the session cookie (Next never sees it, §1d), so the server cannot
// name the job. The view sets `document.title` when the read lands.
export const metadata: Metadata = { title: "Job" };

export default async function DashboardJobPage({ params }: PageProps<"/dashboard/jobs/[id]">) {
  const { id } = await params;
  return <JobDetailView jobId={id} />;
}
