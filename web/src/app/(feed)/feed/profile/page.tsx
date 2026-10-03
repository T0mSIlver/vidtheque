import type { Metadata } from "next";
import { ProfileView } from "./ProfileView";

// Entries and history are read in the browser (dashboard.md §25.5).
export const metadata: Metadata = { title: "Profile" };

export default function FeedProfilePage() {
  return <ProfileView />;
}
