// Ten flat hits are usually three talks. The server ranks and paginates; the
// page groups what it was handed and nothing else (demo-site.md §6.5), so a
// card's position is the position of its best hit and never a re-ranking.
import type { Hit } from "@/lib/api";

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
      group = { video_id: hit.video_id, title: hit.title, channel: hit.channel, thumb: null, hits: [] };
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
