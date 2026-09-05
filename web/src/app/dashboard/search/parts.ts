// The four derivations the owner's search page performs on a hit, and the leg
// table it reads `leg_counts` through.
//
// Every one of them is a *rendering* of a field the payload already carries
// (dashboard.md §14.2's table): the receipt is `link` checked, the in-index
// link is `video_id` and `frame_id` with the strip's own arithmetic, the
// evidence badges are `source` in the demo's three words, and the marked runs
// are `text` and the query. Nothing here queries anything, and nothing here
// builds markup — `highlight` returns text runs with a flag, and the component
// decides what a marked run looks like.
//
// The Jinja page these replace is `dashboard/views.py`'s `_search_receipt`,
// `_search_inside`, `_search_evidence`, `_highlighted` and `_search_legs`; each
// function below is named after the one it replaces and is tested against its
// cases, because two surfaces disagreeing about which second a hit happened at
// is worse than either of them being wrong.

import type { Hit } from "@/lib/api/schemas";
import { ROOT } from "@/lib/dashboard/client";
import { badges, type Badge } from "@/lib/group";

/** `read_models.FRAME_PAGE` — how many keyframes a strip page holds.
 *
 *  Reimplemented rather than read off a payload, because the link is built
 *  before the video's own detail has been fetched. It is the *default* the
 *  detail endpoint applies when no `frames=` is asked for, and this link asks
 *  for none, so the page the reader lands on is paged by exactly this number.
 *  A `?frames=` in the URL over there moves the strip and the fragment still
 *  finds the frame. */
export const FRAME_PAGE = 24;

/** The receipt: the tool's YouTube link, admitted as one exact-second proof.
 *
 *  `views._search_receipt`. HTTPS, `youtu.be`, a video id, and a `t=` that is
 *  digits — a link that is anything else is not printed at all, rather than
 *  printed unchecked. The page never reconstructs a timestamp from display text
 *  or invents a link for a source that has none (dashboard.md §14). */
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
  if (seconds === null || !/^\d+$/.test(seconds)) return null;
  return { href: link, label: `youtu.be/${id}?t=${seconds}` };
}

/** The other link on the row: the same moment *inside this deployment*.
 *
 *  `views._search_inside`. The receipt goes to YouTube, which is the product's
 *  argument; this goes to what the index actually stored about that second,
 *  which is what this surface is for.
 *
 *  A hit that names a keyframe lands **on that frame**: `ord` is dense per
 *  video and the strip pages by ordinal, so which page holds it is arithmetic
 *  rather than a second query — the same arithmetic the shot bars do — and
 *  `select=` plus `#frame-N` mark it whether or not the script runs.
 *
 *  A transcript hit does not get the same treatment, and that is deliberate: it
 *  names its cues by **id**, the transcript panel pages by *offset*, and there
 *  is no honest arithmetic from one to the other. It links to the video plainly
 *  and lets the panel say where it is. */
export function insideLink(hit: Pick<Hit, "video_id" | "frame_id">): string | null {
  if (!hit.video_id) return null;
  const page = `${ROOT}/videos/${encodeURIComponent(hit.video_id)}`;
  const prefix = `${hit.video_id}-`;
  const frameId = hit.frame_id;
  if (!frameId || !frameId.startsWith(prefix)) return page;
  const tail = frameId.slice(prefix.length);
  if (!/^\d+$/.test(tail)) return page;
  const ordinal = Number(tail);
  const offset = Math.floor(ordinal / FRAME_PAGE) * FRAME_PAGE;
  return `${page}?frame_offset=${offset}&select=${ordinal}#frame-${ordinal}`;
}

/** Which of the four ways the snippet under a hit is set. `screen` is the one
 *  allowed lime, because it is the only one that is text the machine read off
 *  a slide (The Lime Rule). */
export type EvidenceKind = "spoken" | "screen" | "frame" | "mixed" | "other";

export interface Evidence {
  /** The tool's own `source` string, kept: this is an instrument, and
   *  `source=transcript+ocr` is what a bug report quotes. */
  key: string;
  pills: { label: string; kind: EvidenceKind }[];
  kind: EvidenceKind;
}

const KINDS: Record<Badge, EvidenceKind> = {
  spoken: "spoken",
  "on-screen": "screen",
  frame: "frame",
};

/** `source` as words a human reads, with the key kept (`views._search_evidence`).
 *
 *  The words are `lib/group`'s — the demo's own — so a badge means the same
 *  thing on both surfaces. A source that table does not know still gets a badge
 *  carrying its own name: a fourth leg one day must arrive as an unfamiliar
 *  word, never as a hit with no provenance on it at all. */
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

// Enough terms to mark a real question, few enough that a query pasted out of a
// log cannot turn a snippet into a solid block of gold; and a ceiling on the
// marks themselves, because the text this runs over is capped by the tool but
// the number of occurrences in it is not.
export const HIGHLIGHT_TERMS = 8;
export const HIGHLIGHT_MARKS = 40;

export interface Run {
  text: string;
  hit: boolean;
}

/** The snippet, split into the parts the query matched and the parts it did not
 *  — never into markup (`views._highlighted`).
 *
 *  Not the tool's own matching: the FTS leg stems and the vector legs do not
 *  match words at all, so a mark is "these are your words, here" and never a
 *  claim about *why* this hit ranked. A hit with nothing marked is ordinary —
 *  that is what a semantic match looks like. */
export function highlight(text: string | null, query: string): Run[] {
  if (!text) return [];
  const seen = new Set<string>();
  for (const word of query.toLowerCase().split(/[^\p{L}\p{N}_'-]+/u)) {
    if (word.length > 1) seen.add(word);
  }
  // Longest first, so a short term cannot shadow the long one containing it:
  // alternation matches leftmost-first, not leftmost-longest.
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

// The query planner's own leg names, in a human's words (`views._LEG_LABELS`).
// The count line under a search is the one thing tool-surface.md tells callers
// to *read* — `fts 0` is how you learn the corpus does not contain your
// phrasing — so this page prints it as a sentence rather than as eight
// identifiers, with the raw key beside each label because an operator comparing
// this page against a `search` payload is comparing keys, not prose.
//
// The units are part of the labels because the numbers are three different
// units and are famously not summands (tool-surface.md §9.2): the fused leg
// counts segments, `fts` counts cues, the vector legs count chunks and frames,
// and `…_knn` is what the nearest-neighbour search *considered* before the
// relevance band cut it down.
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

/** A fused leg is a peer; the three `…_knn`/`…_vec` counts explain one. */
const FUSED = new Set(["transcript", "ocr", "frame"]);

export interface Leg {
  key: string;
  label: string;
  unit: string;
  count: number;
  /** A candidate count behind a fused leg: set one step back rather than
   *  printed as a ninth peer. */
  sub: boolean;
}

/** `leg_counts` in reading order, each with its label, unit and raw key
 *  (`views._search_legs`).
 *
 *  Ordered by this table rather than by the payload, so a sub-leg always sits
 *  under the leg it explains; a key the table does not know is appended with
 *  its own name for a label rather than dropped, for the same reason an
 *  unfamiliar `source` still gets a badge. */
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
