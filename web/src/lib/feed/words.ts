// The feed's words and the prompts it hands to Claude (companion.md §2.2, §3.1, §6).

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

/** Names the corpus and the video, and leaves the question to you. */
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
    `with their timestamps.\n\nMy question: `
  );
}

/** "Ask Claude to build my profile" (§2.2). */
export const PROFILE_PROMPT =
  "Help me build my interest profile in vidtheque. It is a short list of interests in " +
  "plain words, each with a weight from -1 to 1 (negative means less of this), and it " +
  "decides which new videos from the channels I follow are worth my time. Start from " +
  "what you already know about me and my work. If that is not enough, interview me " +
  "with a few short questions first. Show me the list, then save it with the vidtheque " +
  "profile tool: call it bare to see what is there, add what is missing, and do not " +
  "drop entries I wrote.";

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
