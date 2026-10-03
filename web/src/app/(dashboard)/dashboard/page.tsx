import type { Metadata } from "next";
import { HealthView } from "./HealthView";

// A data-free shell; the browser reads `/dashboard/api/health` (dashboard.md §24.1).
export const metadata: Metadata = { title: "Health" };

export default function DashboardHealthPage() {
  return <HealthView />;
}
