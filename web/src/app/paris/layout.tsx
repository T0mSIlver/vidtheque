import type { Metadata } from "next";
import DemoLayout from "@/app/demo/layout";

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

export default function ParisLayout({ children, params }: LayoutProps<"/paris">) {
  // The demo chrome, minus the corpus count: beside this wordmark a
  // whole-corpus total would read as the size of the edition (4.4).
  return (
    <DemoLayout params={params} count={null}>
      {children}
    </DemoLayout>
  );
}
