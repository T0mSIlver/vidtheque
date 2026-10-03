import type { Metadata } from "next";
import { CorpusView } from "./CorpusView";

// A data-free shell; the browser reads `/dashboard/api/corpus` (dashboard.md §24.1).
export const metadata: Metadata = { title: "Corpus" };

export default function DashboardCorpusPage() {
  return <CorpusView />;
}
