import type { Metadata } from "next";
import { IndexView } from "./IndexView";

// `GET /dashboard/index` — the form that queues a batch (dashboard.md §5.5).
// The last `/dashboard` GET to come off Jinja.
//
// It reads nothing: the deployment's own facts arrive with the chassis, so the
// shell this serves is the finished page rather than a frame around a payload.
//
// The `POST` to this same path is **Python's** and stays Python's — one path,
// two owners, split by method (frontend-migration.md §1d). Nothing in this app
// answers a `POST` to it, so in development the `/dashboard` catch-all forwards
// the write and in production the reverse proxy routes it.
export const metadata: Metadata = { title: "Add to the index" };

export default function DashboardIndexPage() {
  return <IndexView />;
}
