// Ten flat hits are usually three talks. The server ranks and paginates; the
// page groups what it was handed and nothing else (demo-site.md §6.5), so a
// card's position is the position of its best hit and never a re-ranking.
// `schemas` and not the package index: the index is `server-only`, and the
// dashboard's search page groups the same hits in the browser.
import type { Hit } from "@/lib/api/schemas";

export interface VideoGroup {
  video_id: string;
  title: string;
  channel: string;
  /** The first hit's frame, or the first frame any hit has. */
  thumb: string | null;
  hits: Hit[];
}

export function groupByVideo(hits: readonly Hit[]): VideoGroup[] {
  const groups = new Map<string, VideoGroup>();
  for (const hit of hits) {
    let group = groups.get(hit.video_id);
    if (!group) {
      group = {
        video_id: hit.video_id,
        title: hit.title,
        channel: hit.channel,
        thumb: null,
        hits: [],
      };
      groups.set(hit.video_id, group);
    }
    group.hits.push(hit);
    group.thumb ??= hit.thumb;
  }
  return [...groups.values()];
}

// The three kinds of evidence, as words (demo-site.md §6.3). `source` can be
// one leg or a fusion of legs ("ocr+frame"), so this reads it as a set.
export type Badge = "spoken" | "on-screen" | "frame";

export function badges(source: string): Badge[] {
  const legs = new Set(source.split("+"));
  const out: Badge[] = [];
  if (legs.has("transcript")) out.push("spoken");
  if (legs.has("ocr")) out.push("on-screen");
  if (legs.has("frame")) out.push("frame");
  return out;
}

// The word a placeholder prints when a moment has no frame behind it: the
// channel it came from, rather than a blank box (`app.js`'s `placeholder`).
export function channelWord(source: string): string {
  return badges(source)[0] ?? "video";
}

// The four ways a snippet is set, one per provenance (demo-site.md §6.3).
// `mixed` is neither a quote nor screen text: both channels agreed and the text
// is whichever was longer, so it is presented as neither.
export type Presentation = "spoken" | "screen" | "frame" | "mixed";

export function presentationOf(source: string): Presentation {
  const kinds = badges(source);
  if (kinds.length > 1) return "mixed";
  if (kinds[0] === "on-screen") return "screen";
  if (kinds[0] === "frame") return "frame";
  return "spoken";
}

/** One stretch of a snippet, and whether the query put it there. */
export interface Run {
  text: string;
  hit: boolean;
}

// Mark the query's own words inside a snippet. The demo's rule, kept as it was
// (`app.js`'s `highlight`): a term is three characters or more — shorter ones
// mark the joins between words rather than the words — and at each position the
// *earliest* remaining occurrence of any term wins, so the marks come out in
// reading order and never overlap.
//
// Deliberately not the dashboard's `highlight` (`app/dashboard/search/parts.ts`),
// which admits two-character terms and prefers the longest match: that page is
// an instrument for an operator reading a query plan, and this one is a demo.
// Two surfaces, two rules, each with the tests of the page it belongs to.
//
// Runs, never markup: the caller decides what a marked run looks like, and
// corpus text never becomes HTML on the way (demo-site.md §6.2).
export function highlight(text: string | null, query: string): Run[] {
  if (!text) return [];
  const terms = (query || "")
    .toLowerCase()
    .split(/[^\p{L}\p{N}]+/u)
    .filter((term) => term.length > 2);
  if (!terms.length) return [{ text, hit: false }];

  const lower = text.toLowerCase();
  const runs: Run[] = [];
  let cursor = 0;
  while (cursor < text.length) {
    let best = -1;
    let length = 0;
    for (const term of terms) {
      const at = lower.indexOf(term, cursor);
      if (at !== -1 && (best === -1 || at < best)) {
        best = at;
        length = term.length;
      }
    }
    if (best === -1) {
      runs.push({ text: text.slice(cursor), hit: false });
      return runs;
    }
    if (best > cursor) runs.push({ text: text.slice(cursor, best), hit: false });
    runs.push({ text: text.slice(best, best + length), hit: true });
    cursor = best + length;
  }
  return runs;
}
