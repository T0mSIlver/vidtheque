import type { Metadata } from "next";
import { VideoDetailView } from "./VideoDetailView";

// `GET /dashboard/videos/{video_id}` — the panels dashboard.md §5.3 describes,
// reading `/dashboard/api/library/{video_id}` in the browser.
//
// The title here is the generic one and stays that way: this shell is rendered
// without the session cookie (Next never sees it, §1d), so the server cannot
// know the video's name. The view sets `document.title` when the read lands,
// which is the first moment anything knows it.
export const metadata: Metadata = { title: "Video" };

export default async function DashboardVideoPage({ params }: PageProps<"/dashboard/videos/[id]">) {
  const { id } = await params;
  return <VideoDetailView videoId={id} />;
}
