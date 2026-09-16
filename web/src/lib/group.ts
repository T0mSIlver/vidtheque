// Grouping and provenance words for hits. The server ranks and paginates; the
// page groups what it was handed and never re-ranks (demo-site.md §6.5).
// `lib/schemas`, not the api package index: that index is server-only.
import type { Hit } from "@/lib/schemas/search";
import { badges, type Badge } from "@/lib/schemas/evidence";

export { badges, type Badge };

export interface VideoGroup {
  video_id: string;
  title: string;
  channel: string;
  /** The first frame any hit in the group has. */
  thumb: string | null;
  hits: Hit[];
}

/** Cards in order of each video's first appearance. */
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

const LEG_WORD: Record<string, Badge> = {
  transcript: "spoken",
  ocr: "on-screen",
  frame: "frame",
};

/**
 * The badges a row prints. Provenance is never dropped silently: a leg this
 * build does not know prints the whole raw `source`, since "spoken" beside a
 * dropped half would be a claim.
 */
export function badgeWords(source: string): string[] {
  if (!source) return [];
  const legs = source.split("+");
  if (legs.some((leg) => LEG_WORD[leg] === undefined)) return [source];
  return badges(source);
}

/** A frameless moment's placeholder word; an unknown leg says "video". */
export function channelWord(source: string): string {
  for (const leg of source.split("+")) {
    if (LEG_WORD[leg]) return LEG_WORD[leg];
  }
  return "video";
}

// How a snippet is set (demo-site.md §6.3). `mixed` is neither a quote nor
// screen text, and neither is a leg this build cannot vouch for.
export type Presentation = "spoken" | "screen" | "frame" | "mixed";

export function presentationOf(source: string): Presentation {
  const legs = source.split("+");
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

// Terms of three characters or more; the earliest occurrence wins, so marks
// come out in reading order and never overlap. Deliberately not the
// dashboard's rule (`app/dashboard/search/parts.ts`). Runs, never markup.
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
