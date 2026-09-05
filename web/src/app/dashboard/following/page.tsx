import type { Metadata } from "next";
import { FollowingView } from "./FollowingView";

// `GET /dashboard/following` — the follows table, its budget and the form that
// makes a rule (dashboard.md §18.3). A shell with no data in it: the reading is
// the browser's, against `/dashboard/api/following` with the session cookie.
//
// The `POST` to this same path is **Python's** and stays Python's. The matcher
// in `proxy.ts` is path-only, so production has to route this one by method:
// `GET /dashboard/following` to Next, `POST /dashboard/following` to Python
// (frontend-migration.md §1d's last row).
export const metadata: Metadata = { title: "Following" };

export default function DashboardFollowingPage() {
  return <FollowingView />;
}
