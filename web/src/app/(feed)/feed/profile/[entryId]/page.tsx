import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { CollectionView } from "./CollectionView";

// One entry's moments, read in the browser (dashboard.md §25.14).
export const metadata: Metadata = { title: "Collection" };

export default async function FeedCollectionPage({ params }: PageProps<"/feed/profile/[entryId]">) {
  const { entryId } = await params;
  if (!/^\d{1,11}$/.test(entryId)) notFound();
  return <CollectionView entryId={Number(entryId)} />;
}
