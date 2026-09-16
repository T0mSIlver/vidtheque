// What the owner's search page derives from a hit, each a rendering of a field
// the payload carries (dashboard.md §14.2). Each function mirrors the
// `dashboard/views.py` helper it names and is tested against its cases.

import type { Hit } from "@/lib/api/schemas";
import { ROOT } from "@/lib/dashboard/client";
import { badges, type Badge } from "@/lib/group";

/** `read_models.FRAME_PAGE`: the detail strip's default page size, which the
 *  in-index link pages by (it asks for no `frames=`). */
export const FRAME_PAGE = 24;

/** Every Unicode decimal digit, as Python's `str.isdigit()` and `int()` read. */
const DIGITS = /^\p{Nd}+$/u;

/** One `Nd` digit's value: its offset into its script's run of ten. */
function digitValue(digit: string): number {
  const point = digit.codePointAt(0) as number;
  let zero = point;
  while (zero > 0 && /\p{Nd}/u.test(String.fromCodePoint(zero - 1))) zero -= 1;
  return (point - zero) % 10;
}

function intOf(digits: string): number {
  if (/^[0-9]+$/.test(digits)) return Number(digits);
  let value = 0;
  for (const digit of digits) value = value * 10 + digitValue(digit);
  return value;
}

/** `_search_receipt`: an HTTPS `youtu.be` link with a digit `t=`, or nothing —
 *  never an unchecked link, never a reconstructed timestamp (§14). */
export function receiptOf(link: string): { href: string; label: string } | null {
  let url: URL;
  try {
    url = new URL(link);
  } catch {
    return null;
  }
  const seconds = url.searchParams.get("t");
  const id = url.pathname.replace(/^\/+|\/+$/g, "");
  if (url.protocol !== "https:" || url.hostname !== "youtu.be" || !id) return null;
  if (seconds === null || !DIGITS.test(seconds)) return null;
  return { href: link, label: `youtu.be/${id}?t=${seconds}` };
}

/** `_search_inside`: the same moment inside this deployment. A frame hit lands
 *  on its frame (the strip page is arithmetic on the dense ordinal); a
 *  transcript hit names cues by id, which has no offset, so it links plainly. */
export function insideLink(hit: Pick<Hit, "video_id" | "frame_id">): string | null {
  if (!hit.video_id) return null;
  const page = `${ROOT}/videos/${encodeURIComponent(hit.video_id)}`;
  const prefix = `${hit.video_id}-`;
  const frameId = hit.frame_id;
  if (!frameId || !frameId.startsWith(prefix)) return page;
  const tail = frameId.slice(prefix.length);
  if (!DIGITS.test(tail)) return page;
  const ordinal = intOf(tail);
  const offset = Math.floor(ordinal / FRAME_PAGE) * FRAME_PAGE;
  return `${page}?frame_offset=${offset}&select=${ordinal}#frame-${ordinal}`;
}

/** How a snippet is set; `screen` is the one allowed lime (The Lime Rule). */
export type EvidenceKind = "spoken" | "screen" | "frame" | "mixed" | "other";

export interface Evidence {
  /** The tool's own `source`, kept for bug reports. */
  key: string;
  pills: { label: string; kind: EvidenceKind }[];
  kind: EvidenceKind;
}

const KINDS: Record<Badge, EvidenceKind> = {
  spoken: "spoken",
  "on-screen": "screen",
  frame: "frame",
};

/** `_search_evidence`: `source` in the demo's words (`lib/group`); an unknown
 *  source still arrives as a badge carrying its own name. */
export function evidenceOf(source: string): Evidence {
  const key = source || "";
  const words = badges(key);
  if (!words.length) {
    return key
      ? { key, pills: [{ label: key, kind: "other" }], kind: "other" }
      : { key: "", pills: [], kind: "other" };
  }
  const pills = words.map((word) => ({ label: word, kind: KINDS[word] }));
  return { key, pills, kind: pills.length > 1 ? "mixed" : pills[0].kind };
}

// Bounds on a pasted query: how many terms are marked, and how many marks.
export const HIGHLIGHT_TERMS = 8;
export const HIGHLIGHT_MARKS = 40;

export interface Run {
  text: string;
  hit: boolean;
}

/** `_highlighted`: the snippet split into runs the query's words matched.
 *  "Your words, here", never a claim about why a hit ranked. */
export function highlight(text: string | null, query: string): Run[] {
  if (!text) return [];
  const seen = new Set<string>();
  for (const word of query.toLowerCase().split(/[^\p{L}\p{N}_'-]+/u)) {
    if (word.length > 1) seen.add(word);
  }
  // Longest first: alternation is leftmost-first, not leftmost-longest.
  const terms = [...seen].sort((a, b) => b.length - a.length).slice(0, HIGHLIGHT_TERMS);
  if (!terms.length) return [{ text, hit: false }];

  const pattern = new RegExp(terms.map(escapeRe).join("|"), "giu");
  const runs: Run[] = [];
  let cursor = 0;
  let marks = 0;
  for (const match of text.matchAll(pattern)) {
    if (marks >= HIGHLIGHT_MARKS) break;
    marks += 1;
    if (match.index > cursor) runs.push({ text: text.slice(cursor, match.index), hit: false });
    runs.push({ text: match[0], hit: true });
    cursor = match.index + match[0].length;
  }
  if (cursor < text.length) runs.push({ text: text.slice(cursor), hit: false });
  return runs;
}

function escapeRe(term: string): string {
  return term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// `_LEG_LABELS`: the planner's leg keys in words, with units, because the
// counts are different units and not summands (tool-surface.md §9.2).
const LEG_LABELS: [string, string, string][] = [
  ["transcript", "Transcript — ranked into these results", "segments"],
  ["transcript_fts", "Transcript — keyword match (FTS)", "cues"],
  ["transcript_vec", "Transcript — semantic match (embeddings)", "chunks kept"],
  ["transcript_vec_knn", "Transcript — semantic candidates considered", "chunks"],
  ["ocr", "On-screen text (OCR)", ""],
  ["frame", "Frames — visual match", ""],
  ["frame_vec", "Frames — semantic match (embeddings)", "frames kept"],
  ["frame_knn", "Frames — visual candidates considered", "frames"],
];

/** A fused leg is a peer; the others explain one. */
const FUSED = new Set(["transcript", "ocr", "frame"]);

export interface Leg {
  key: string;
  label: string;
  unit: string;
  count: number;
  /** A candidate count behind a fused leg, set one step back. */
  sub: boolean;
}

/** `_search_legs`: in table order, unknown keys appended under their own name. */
export function legsOf(counts: Record<string, number> | undefined): Leg[] {
  if (!counts) return [];
  const known = LEG_LABELS.map(([key]) => key);
  const labels = new Map(LEG_LABELS.map(([key, label, unit]) => [key, { label, unit }]));
  const order = [...known, ...Object.keys(counts).filter((key) => !known.includes(key))];
  const legs: Leg[] = [];
  for (const key of order) {
    if (!(key in counts)) continue;
    const named = labels.get(key) ?? { label: key, unit: "" };
    legs.push({ key, ...named, count: counts[key], sub: !FUSED.has(key) });
  }
  return legs;
}
