import type { Metadata } from "next";
import { SearchView } from "./SearchView";

// A data-free shell; the browser reads `/dashboard/api/search` (dashboard.md §14).
export const metadata: Metadata = { title: "Search" };

export default function DashboardSearchPage() {
  return <SearchView />;
}
