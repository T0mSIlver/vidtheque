import type { Metadata } from "next";
import { Chrome } from "./Chrome";

// The management surface's own segment. Everything under it wears the chassis:
// the rail, the content column and the signature at the foot (`Chrome`).
//
// `noindex, nofollow`, exactly as `templates/base.html` has always sent: this
// surface is an instrument, its pages are behind a gate, and a demo instance's
// projection of them is still not a page anyone should reach from a search
// result.
export const metadata: Metadata = {
  title: { default: "Dashboard", template: "%s — vidtheque" },
  robots: { index: false, follow: false },
};

export default function DashboardLayout({ children }: LayoutProps<"/dashboard">) {
  return <Chrome>{children}</Chrome>;
}
