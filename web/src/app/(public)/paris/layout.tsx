import type { Metadata } from "next";
import { PublicShell } from "@/components/public/PublicShell";

export const metadata: Metadata = {
  title: { absolute: "vidtheque — AI Engineer Paris 2026" },
  description:
    "Every main-stage talk from AI Engineer Paris, cited to the second, slides included.",
  openGraph: {
    type: "website",
    siteName: "vidtheque",
    title: "AI Engineer Paris 2026 — every main-stage talk, cited",
    description:
      "Every main-stage talk from AI Engineer Paris, cited to the second, slides included.",
  },
  twitter: { card: "summary" },
};

// No corpus count: beside this wordmark it would read as the edition's size.
export default function ParisLayout({ children }: LayoutProps<"/paris">) {
  return (
    <PublicShell showCount={false} showConnect={false}>
      {children}
    </PublicShell>
  );
}
