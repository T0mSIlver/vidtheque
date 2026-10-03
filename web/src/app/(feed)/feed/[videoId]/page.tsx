import type { Metadata } from "next";
import { VideoView } from "./VideoView";

// The verdict is read in the browser (dashboard.md §25.3).
export const metadata: Metadata = { title: "Video" };

export default async function FeedVideoPage({ params }: PageProps<"/feed/[videoId]">) {
  const { videoId } = await params;
  return <VideoView videoId={videoId} />;
}
