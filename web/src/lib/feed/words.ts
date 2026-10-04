// The feed's words and the prompts it hands to Claude (companion.md §2.2, §3.1, §6).

import { clock } from "@/lib/format";

/** What each score asks of you; the score always prints its word. */
export const SCORE_WORDS: Record<number, string> = {
  0: "skip",
  1: "the summary is enough",
  2: "watch the moments",
  3: "watch it whole",
};

export function scoreWord(score: number): string {
  return SCORE_WORDS[score] ?? `score ${score}`;
}

/** Claude with a prompt filled in. Whether the Android app keeps `q` is
 *  unverified (§6), so the page also offers the prompt to copy. */
export function claudeUrl(prompt: string): string {
  return `https://claude.ai/new?q=${encodeURIComponent(prompt)}`;
}

/** What Claude saves from an Ask Claude conversation (#159). */
export const PROFILE_NOTE =
  "If our conversation shows a topic I care about or am tired of, save it with the " +
  "vidtheque profile tool: a topic of 2-4 words, a weight from -1 to 1 and a one-line " +
  "reason. Topics only: no companies, people, pay or job details.";

/** Names the corpus and the video, leaves the question to you, and has Claude save what it learns. */
export function videoPrompt(video: {
  video_id: string;
  title: string;
  channel: string | null;
}): string {
  const by = video.channel ? ` by ${video.channel}` : "";
  return (
    `I have a question about a video in my vidtheque corpus: "${video.title}"${by}, ` +
    `video id ${video.video_id}. Read it with the vidtheque tools (video-summary first, ` +
    `then get-segment-context where you need the detail) and cite the youtu.be links ` +
    `with their timestamps. ${PROFILE_NOTE}\n\nMy question: `
  );
}

/** The on-demand interview (§2.2). */
export const PROFILE_PROMPT =
  "Interview me to build my interest profile in vidtheque, which decides which new videos " +
  "from the channels I follow are worth my time. Ask me these five questions one at a time, " +
  "and keep each answer short: 1. What am I building right now? 2. What do I want to learn " +
  "next? 3. What do I already know well enough to skip the basics of? 4. What am I tired of " +
  "hearing about? 5. What kind of video is worth my time? Then show me the list you would " +
  "save: topics of 2-4 words, each weighted from -1 (less of this) to 1, and what I am " +
  'building as kind "project". Topics only: no companies, people, pay or job details. ' +
  "Once I agree, save it with the vidtheque profile tool: call it bare to see what is there, " +
  "add what is missing, and do not drop entries I wrote.";

/** "project, 12 days left": a project lapses unless written again (#159). */
export function lapses(expiresAt: number, now = Date.now() / 1000): string {
  const days = Math.max(0, Math.ceil((expiresAt - now) / 86_400));
  return `project, ${days} ${days === 1 ? "day" : "days"} left`;
}

/** The weight a pasted line gets when it names none. */
export const DEFAULT_WEIGHT = 0.5;

export type PastedEntry = { text: string; weight: number };

// A bullet or a list number in front, then the text, then an optional weight
// at either end: "+0.9 evals", "evals (−0.8)", "evals: 0.6". A bare number
// counts only with a sign or a point, so "1 billion users" and "Python 3.10"
// stay text.
const BULLET = /^\s*(?:[-*•·]|\d+[.)])\s+/;
const WEIGHT = String.raw`([+\-−]?(?:1(?:\.0+)?|0?\.\d+|0))`;
const BARE = String.raw`([+\-−](?:1(?:\.0+)?|0?\.\d+|0)|0?\.\d+|1\.0+)`;
const LEADING = new RegExp(String.raw`^${BARE}\s+(.+)$`);
const MARKED = new RegExp(String.raw`^(.+?)\s*(?::|\(|\[)\s*${WEIGHT}\s*[)\]]?$`);
const TRAILING = new RegExp(String.raw`^(.+?)\s+${BARE}$`);

/** One entry per non-empty line (§2.2's fallback), weights clamped to [-1, 1]. */
export function parsePasted(raw: string): PastedEntry[] {
  const entries: PastedEntry[] = [];
  for (const line of raw.split(/\r?\n/)) {
    const bare = line.replace(BULLET, "").trim();
    if (!bare) continue;
    let text = bare;
    let weight = DEFAULT_WEIGHT;
    const leading = LEADING.exec(bare);
    const trailing = MARKED.exec(bare) ?? TRAILING.exec(bare);
    if (leading) {
      weight = toWeight(leading[1]);
      text = leading[2];
    } else if (trailing) {
      weight = toWeight(trailing[2]);
      text = trailing[1];
    }
    text = text.replace(/[\s:–—-]+$/, "").trim();
    if (text) entries.push({ text, weight });
  }
  return entries;
}

function toWeight(raw: string): number {
  const value = Number(raw.replace("−", "-"));
  return Math.max(-1, Math.min(1, value));
}

/** `+0.9`, `-0.8`, `0.0`: a weight always prints its sign. */
export function signed(weight: number): string {
  const fixed = Math.abs(weight).toFixed(1);
  if (fixed === "0.0") return "0.0";
  return `${weight < 0 ? "−" : "+"}${fixed}`;
}

/** What a moment repeats, and what its link skips (companion.md §3.2). `where`
 *  is the video you saw it in, or, on a collection, the moment above it. */
export function repeatNote(where: string, whole: boolean, skippedS: number): string {
  if (whole) return `You saw all of it in ${where}`;
  if (skippedS > 0) return `Skips ${clock(skippedS)} you saw in ${where}`;
  return `You saw part of it in ${where}`;
}
