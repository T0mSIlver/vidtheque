import type { Metadata } from "next";
import { FollowDetailView } from "./FollowDetailView";

// The server cannot name the follow; the view renames the document (§18.4).
export const metadata: Metadata = { title: "Following" };

export default async function DashboardFollowPage({
  params,
}: PageProps<"/dashboard/following/[slug]">) {
  const { slug } = await params;
  return <FollowDetailView key={slug} slug={slug} />;
}
