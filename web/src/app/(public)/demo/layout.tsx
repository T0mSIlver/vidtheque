import type { Metadata } from "next";
import { PublicShell } from "@/components/public/PublicShell";

const DESCRIPTION =
  "Which AI Engineer 2026 talks will teach you something, and which minutes. Then ask them.";

// No `og:image`: a wrong one is worse than none (demo-site.md §6.1).
export const metadata: Metadata = {
  title: { absolute: "vidtheque — the sample feed" },
  description: DESCRIPTION,
  openGraph: {
    type: "website",
    siteName: "vidtheque",
    title: "vidtheque — the sample feed",
    description: DESCRIPTION,
  },
  twitter: { card: "summary" },
};

export default function DemoLayout({ children }: LayoutProps<"/demo">) {
  return <PublicShell>{children}</PublicShell>;
}
