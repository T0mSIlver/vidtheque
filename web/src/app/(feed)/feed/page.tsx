import type { Metadata } from "next";
import { WeekView } from "./WeekView";

// A data-free shell; the browser reads `/dashboard/api/week` (dashboard.md §25.13).
export const metadata: Metadata = { title: "Feed" };

export default function FeedPage() {
  return <WeekView />;
}
