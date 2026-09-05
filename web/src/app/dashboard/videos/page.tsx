import type { Metadata } from "next";
import { VideosView } from "./VideosView";

// `GET /dashboard/videos` — the table, its filters and its exact count
// (dashboard.md §5.2). A shell with no data in it: the reading is the
// browser's, against `/dashboard/api/library` with the session cookie.
export const metadata: Metadata = { title: "Videos" };

export default function DashboardVideosPage() {
  return <VideosView />;
}
