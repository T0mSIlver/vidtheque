import type { Metadata } from "next";
import { refuseWithoutDashboard } from "@/lib/dashboard/presence";
import { Chrome } from "@/components/dashboard/Chrome";

// Every page under `/dashboard` wears the chassis. `noindex`: an instrument
// behind a gate, even in a demo's projection.
export const metadata: Metadata = {
  title: { default: "Dashboard", template: "%s — vidtheque" },
  robots: { index: false, follow: false },
};

export default async function DashboardLayout({ children }: LayoutProps<"/dashboard">) {
  await refuseWithoutDashboard();
  return <Chrome>{children}</Chrome>;
}
