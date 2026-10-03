import type { Metadata } from "next";
import { FeedView } from "./FeedView";

// A data-free shell; the browser reads `/dashboard/api/feed` (dashboard.md §25.2).
export const metadata: Metadata = { title: "Feed" };

export default function FeedPage() {
  return <FeedView />;
}
