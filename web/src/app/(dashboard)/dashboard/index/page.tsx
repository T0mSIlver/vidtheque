import type { Metadata } from "next";
import { IndexView } from "./IndexView";

// The GET is Next's; the POST to this path stays Python's (frontend-migration.md §1d).
export const metadata: Metadata = { title: "Add to the index" };

export default function DashboardIndexPage() {
  return <IndexView />;
}
