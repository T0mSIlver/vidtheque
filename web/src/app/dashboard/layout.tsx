import type { Metadata } from "next";
import { Chrome } from "./Chrome";

// Every page under `/dashboard` wears the chassis. `noindex`: an instrument
// behind a gate, even in a demo's projection.
export const metadata: Metadata = {
  title: { default: "Dashboard", template: "%s — vidtheque" },
  robots: { index: false, follow: false },
};

export default function DashboardLayout({ children }: LayoutProps<"/dashboard">) {
  return <Chrome>{children}</Chrome>;
}
