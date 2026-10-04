import type { Metadata } from "next";
import { Suspense } from "react";
import { Rail } from "@/components/public/Rail";
import { RailFacts } from "@/components/public/facts";
import { Landing } from "@/components/public/landing/Landing";
import { readFeed } from "@/lib/api/search";

// The landing (DESIGN.md's reference surface). Copy is bound by positioning.md.

const LEAD =
  "vidtheque watches the channels you follow and tells you which videos, and which minutes, will teach you something.";

export const metadata: Metadata = {
  title: { absolute: "vidtheque — which videos, and which minutes" },
  description: LEAD,
  openGraph: {
    type: "website",
    siteName: "vidtheque",
    title: "vidtheque — which videos, and which minutes",
    description: LEAD,
  },
  twitter: { card: "summary" },
};

export default async function LandingPage() {
  const feed = await readFeed({ limit: 3 });
  return (
    <>
      <Rail>
        <Suspense fallback={null}>
          <RailFacts showCount />
        </Suspense>
      </Rail>
      <Landing feed={feed} />
    </>
  );
}
