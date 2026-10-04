import type { Metadata } from "next";
import { refuseWithoutDashboard } from "@/lib/dashboard/presence";
import { FeedShell } from "@/components/feed/FeedShell";

// The phone feed (companion.md §6), behind the console's sign-in. `noindex`:
// it is one owner's reading list.
export const metadata: Metadata = {
  title: { default: "Feed", template: "%s — vidtheque" },
  robots: { index: false, follow: false },
};

export default async function FeedLayout({ children }: LayoutProps<"/feed">) {
  await refuseWithoutDashboard();
  return <FeedShell>{children}</FeedShell>;
}
