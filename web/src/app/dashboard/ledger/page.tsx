import type { Metadata } from "next";
import { LedgerView } from "./LedgerView";

// `GET /dashboard/ledger` — every key number this instance can count
// (dashboard.md §17), on one page instead of four. A shell with no data in it:
// the reading is the browser's, against `/dashboard/api/ledger`.
// `views.ledger`'s own title, and deliberately not the `<h1>`: the heading
// names the page, the document names the tab.
export const metadata: Metadata = { title: "Ledger" };

export default function DashboardLedgerPage() {
  return <LedgerView />;
}
