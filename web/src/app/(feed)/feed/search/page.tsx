import type { Metadata } from "next";
import { Suspense } from "react";
import { SearchView } from "./SearchView";

// A data-free shell; the browser reads `/dashboard/api/search` (dashboard.md §25.9).
export const metadata: Metadata = { title: "Search" };

export default function FeedSearchPage() {
  return (
    <Suspense>
      <SearchView />
    </Suspense>
  );
}
