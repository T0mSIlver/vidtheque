import type { Metadata } from "next";
import { LedgerView } from "./LedgerView";

// `GET /dashboard/ledger` — every key number this instance can count
// (dashboard.md §17), on one page instead of four. A shell with no data in it:
// the reading is the browser's, against `/dashboard/api/ledger`.
export const metadata: Metadata = { title: "The ledger" };

export default function DashboardLedgerPage() {
  return <LedgerView />;
}
