import type { Metadata } from "next";
import { FollowingView } from "./FollowingView";

// A data-free shell; the POST to this path is Python's (frontend-migration.md §1d).
export const metadata: Metadata = { title: "Following" };

export default function DashboardFollowingPage() {
  return <FollowingView />;
}
