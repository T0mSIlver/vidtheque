// The vocabulary of a hit's `source` field, read off the same payload by both
// halves: the demo prints these words as badges, the dashboard maps them onto
// its own pill kinds (`app/dashboard/search/parts.ts`).

/** The three kinds of evidence as words (demo-site.md §6.3). `source` is one
 *  leg or a fusion ("ocr+frame"). */
export type Badge = "spoken" | "on-screen" | "frame";

/** The legs this build knows, in the words it prints. */
export function badges(source: string): Badge[] {
  const legs = new Set(source.split("+"));
  const out: Badge[] = [];
  if (legs.has("transcript")) out.push("spoken");
  if (legs.has("ocr")) out.push("on-screen");
  if (legs.has("frame")) out.push("frame");
  return out;
}
