import type { Metadata } from "next";
import { LedgerView } from "./LedgerView";

// A data-free shell; the browser reads `/dashboard/api/ledger` (dashboard.md §17).
export const metadata: Metadata = { title: "Ledger" };

export default function DashboardLedgerPage() {
  return <LedgerView />;
}
