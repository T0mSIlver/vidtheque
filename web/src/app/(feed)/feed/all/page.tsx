import type { Metadata } from "next";
import { FeedView } from "../FeedView";

// A data-free shell; the browser reads `/dashboard/api/feed?band=all` (dashboard.md §25.2).
export const metadata: Metadata = { title: "All videos" };

export default function AllVideosPage() {
  return <FeedView />;
}
