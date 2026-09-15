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

const LEG_WORD: Record<string, Badge> = {
  transcript: "spoken",
  ocr: "on-screen",
  frame: "frame",
};

/** The legs this build knows, in the words it prints them as. A `source` it
 *  cannot read every leg of is handed back whole by `badgeWords`. */
export function badges(source: string): Badge[] {
  const legs = new Set(source.split("+"));
  const out: Badge[] = [];
  if (legs.has("transcript")) out.push("spoken");
  if (legs.has("ocr")) out.push("on-screen");
  if (legs.has("frame")) out.push("frame");
  return out;
}

/**
 * The badges a result row actually prints.
 *
 * A leg this build has never heard of — a fourth one, one day — gets a badge
 * carrying its own raw name rather than none at all: **dropping the provenance
 * silently is the one thing that must not happen** (`app.js`'s `badgesFor`).
 * One unknown leg takes the whole `source` string with it, because "spoken"
 * printed beside a dropped half is a claim, where `transcript+audio` is only
 * an unfamiliar word.
 */
export function badgeWords(source: string): string[] {
  if (!source) return [];
  const legs = source.split("+");
  if (legs.some((leg) => LEG_WORD[leg] === undefined)) return [source];
  return badges(source);
}

// The word a placeholder prints when a moment has no frame behind it: the
// channel it came from, rather than a blank box (`app.js`'s `placeholder`).
// Deliberately not `badges`: a badge names an unknown leg so the provenance is
// never dropped, while a picture that is missing says "video" — the raw name of
// a leg nobody has heard of is not what belongs in a grey rectangle.
export function channelWord(source: string): string {
  for (const leg of source.split("+")) {
    if (LEG_WORD[leg]) return LEG_WORD[leg];
  }
  return "video";
}

// The four ways a snippet is set, one per provenance (demo-site.md §6.3).
// `mixed` is neither a quote nor screen text: both channels agreed and the text
// is whichever was longer, so it is presented as neither.
export type Presentation = "spoken" | "screen" | "frame" | "mixed";

export function presentationOf(source: string): Presentation {
  const legs = source.split("+");
  // A leg this build does not know is set as neither a quote nor screen text:
  // what its snippet *is* evidence of is precisely what we cannot say, and
  // quotation marks around it would claim somebody said it (`app.js`'s
  // `SNIPPET_CLASS[hit.source] || "snip"`).
  if (legs.some((leg) => LEG_WORD[leg] === undefined)) return "mixed";
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
