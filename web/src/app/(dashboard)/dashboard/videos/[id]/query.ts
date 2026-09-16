import { ROOT } from "@/lib/dashboard/client";
import { pick } from "@/lib/dashboard/query";

// The detail page's address: the strip is bounded by `frames`/`frame_offset`,
// the transcript's place is `cues`/`cue_offset`, and `select` marks a card.

export const FRAME_KEYS = ["frames", "frame_offset"];
const CUE_KEYS = ["cues", "cue_offset"];

/** The cue endpoint's own offset ceiling. */
export const CUE_OFFSET_MAX = 500_000;

/** Rewrite the address bar in place. The URL is the bookmark, not the state:
 *  a browser that refuses the rewrite loses a link and nothing on screen. */
export function rewriteUrl(edit: (url: URL) => boolean | void): void {
  try {
    const here = new URL(window.location.href);
    if (edit(here) === false || here.href === window.location.href) return;
    window.history.replaceState(null, "", here.href);
  } catch {
    // Nothing lost.
  }
}

export function markFrame(ord: number): void {
  rewriteUrl((url) => {
    url.searchParams.set("select", String(ord));
    url.hash = `frame-${ord}`;
  });
}

/** Where the transcript now starts. Paging introduces neither key: the
 *  address stays the one the reader arrived at until they move off the top
 *  or typed a size. */
export function markCues(offset: number, limit: number): void {
  rewriteUrl((url) => {
    const position = offset > 0 || url.searchParams.has("cue_offset");
    const sized = url.searchParams.has("cues");
    if (!position && !sized) return false;
    if (position) url.searchParams.set("cue_offset", String(offset));
    if (sized) url.searchParams.set("cues", String(limit));
    url.hash = "transcript";
  });
}

/** This page at another strip page, optionally marking a frame; the
 *  transcript's two bounds ride along so paging frames keeps its place. */
export function frameLink(
  search: string,
  videoId: string,
  offset: number,
  select: number | null,
  anchor = "",
): string {
  const query = pick(search, [...FRAME_KEYS, ...CUE_KEYS]);
  query.set("frame_offset", String(offset));
  if (select !== null) query.set("select", String(select));
  const fragment = anchor ? `#${anchor}` : select !== null ? `#frame-${select}` : "";
  return `${ROOT}/videos/${encodeURIComponent(videoId)}?${query}${fragment}`;
}

/** This page at another transcript page; the strip stays where it is. */
export function cueLink(search: string, videoId: string, offset: number): string {
  const query = pick(search, [...FRAME_KEYS, ...CUE_KEYS]);
  query.set("cue_offset", String(offset));
  return `${ROOT}/videos/${encodeURIComponent(videoId)}?${query}#transcript`;
}

/** A whole number between `floor` and `ceiling`, or `null`. A size is at least
 *  one cue (`cues=0` would page by nothing); a position may be the top. The
 *  real clamp is still the server's. */
export function cueBound(raw: string | null, ceiling: number, floor: number): number | null {
  if (raw === null || !/^\d+$/.test(raw.trim())) return null;
  return Math.max(floor, Math.min(Number(raw.trim()), ceiling));
}

/** `?select=` as an ordinal, or `null`: no default, since frame 0 marked on
 *  every load would report a click nobody made. */
export function selectedOrd(raw: string | null): number | null {
  if (raw === null || !/^\d+$/.test(raw.trim())) return null;
  return Math.min(Number(raw.trim()), 100_000);
}
