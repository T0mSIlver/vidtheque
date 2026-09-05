import type { Metadata } from "next";
import { FollowDetailView } from "./FollowDetailView";

// `GET /dashboard/following/{slug}` — one follow's rule, its checks and what it
// passed over, reading `/dashboard/api/following/{slug}` in the browser
// (dashboard.md §18.4).
//
// The title here is the generic one and stays that way: this shell is rendered
// without the session cookie (Next never sees it, §1d), so the server cannot
// name the follow. The view sets `document.title` when the read lands.
//
// One segment, like the videos and jobs details, and for the same reason: the
// five `POST /dashboard/following/{slug}/…` routes have three segments under
// their section and stay Python's with no exception written for them.
export const metadata: Metadata = { title: "Following" };

export default async function DashboardFollowPage({
  params,
}: PageProps<"/dashboard/following/[slug]">) {
  const { slug } = await params;
  return <FollowDetailView slug={slug} />;
}
