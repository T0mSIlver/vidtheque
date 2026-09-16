import type { Metadata } from "next";
import { Landing } from "@/components/public/landing/Landing";

// The landing (DESIGN.md's reference surface). Copy is bound by positioning.md;
// the markup is the landing kit's `Landing`, and the page fetches nothing.

export const metadata: Metadata = {
  title: { absolute: "vidtheque — Builders talk. Your agent listens." },
  description: "Empowering AI with the knowledge of the builders and creators.",
  openGraph: {
    type: "website",
    siteName: "vidtheque",
    title: "vidtheque — Builders talk. Your agent listens.",
    description: "Empowering AI with the knowledge of the builders and creators.",
  },
  twitter: { card: "summary" },
};

export default function LandingPage() {
  return <Landing />;
}
