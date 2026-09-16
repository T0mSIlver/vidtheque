import type { Metadata } from "next";
import { OverviewView } from "./OverviewView";

// A data-free shell; the browser reads `/dashboard/api/overview` (dashboard.md
// §5.1). The tab says "Corpus", which tells eight tabs apart better than the h1.
export const metadata: Metadata = { title: "Corpus" };

export default function DashboardOverviewPage() {
  return <OverviewView />;
}
