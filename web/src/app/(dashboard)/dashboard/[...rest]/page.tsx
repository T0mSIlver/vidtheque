import { notFound } from "next/navigation";

// An unmatched path would get the root `not-found.tsx`; catching it here renders
// this segment's own refusal inside the chassis. Python's paths never reach it.
export default function DashboardCatchAll() {
  notFound();
}
