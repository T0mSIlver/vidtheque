import type { Metadata } from "next";
import { BriefView } from "./BriefView";

// The brief is read in the browser (dashboard.md §26.1).
export const metadata: Metadata = { title: "Your week" };

export default async function FeedBriefPage({ searchParams }: PageProps<"/feed/brief">) {
  const { week } = await searchParams;
  return <BriefView week={typeof week === "string" ? week : null} />;
}
