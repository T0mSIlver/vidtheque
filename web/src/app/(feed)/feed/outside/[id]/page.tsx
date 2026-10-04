import type { Metadata } from "next";
import { OutsidePickView } from "./OutsidePickView";

// The pick is read in the browser (dashboard.md §27.2).
export const metadata: Metadata = { title: "From outside" };

export default async function OutsidePickPage({ params }: PageProps<"/feed/outside/[id]">) {
  const { id } = await params;
  return <OutsidePickView id={Number(id)} />;
}
