import type { Metadata } from "next";
import { SearchView } from "./SearchView";

// `GET /dashboard/search` — owner inspection over the corpus (dashboard.md
// §14). A shell with no data in it: the reading is the browser's, against
// `/dashboard/api/search` with the session cookie.
export const metadata: Metadata = { title: "Search" };

export default function DashboardSearchPage() {
  return <SearchView />;
}
