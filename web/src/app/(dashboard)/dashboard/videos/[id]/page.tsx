import type { Metadata } from "next";
import { VideoDetailView } from "./VideoDetailView";

// The server never sees the session cookie; the view names the document (§5.3).
export const metadata: Metadata = { title: "Video" };

export default async function DashboardVideoPage({ params }: PageProps<"/dashboard/videos/[id]">) {
  const { id } = await params;
  return <VideoDetailView key={id} videoId={id} />;
}
