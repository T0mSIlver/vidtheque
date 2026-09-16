import type { Metadata } from "next";
import { VideosView } from "./VideosView";

// A data-free shell; the browser reads `/dashboard/api/library` (dashboard.md §5.2).
export const metadata: Metadata = { title: "Videos" };

export default function DashboardVideosPage() {
  return <VideosView />;
}
