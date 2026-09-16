import type { Metadata } from "next";
import { PublicShell } from "@/components/public/PublicShell";

// No `og:image`: a wrong one is worse than none (demo-site.md §6.1).
export const metadata: Metadata = {
  title: { absolute: "vidtheque — AI Engineer 2026, on tap" },
  description:
    "The knowledge of AI Engineer 2026, on tap. Your agent watched it — ask it something.",
  openGraph: {
    type: "website",
    siteName: "vidtheque",
    title: "vidtheque — AI Engineer 2026, on tap",
    description:
      "The knowledge of AI Engineer 2026, on tap. Your agent watched it — ask it something.",
  },
  twitter: { card: "summary" },
};

export default function DemoLayout({ children }: LayoutProps<"/demo">) {
  return <PublicShell>{children}</PublicShell>;
}
