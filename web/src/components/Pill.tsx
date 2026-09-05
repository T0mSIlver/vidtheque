import styles from "./Pill.module.css";

// Every state prints its own word, in its tone. Colour reinforces; the word
// is the message (DESIGN.md, the Word-and-Colour Rule).
//
// The map is `dashboard/render.py`'s `_TONES`, transcribed, because the rule it
// keeps is the one that matters here: the surface prints the store's word
// verbatim and maps *from* the string for a colour, falling back to neutral on
// anything it does not recognise. A fifth vocabulary is never invented, and an
// unrecognised word renders rather than disappearing. Several words appear in
// more than one of the store's vocabularies and carry the same tone in each,
// which is the point of mapping from the string.
export type Tone = "ok" | "warn" | "bad" | "work" | "wait" | "neutral";

const TONES: Record<string, Tone> = {
  // video.index_state
  ready: "ok",
  indexing: "work",
  pending: "wait",
  stale: "warn",
  failed: "bad",
  // video_stages.state
  done: "ok",
  running: "work",
  skipped: "wait",
  // keyframes.ocr_state
  empty: "wait",
  // data_status, per video and corpus-wide
  ok: "ok",
  no_transcript: "warn",
  no_ocr: "warn",
  no_frames: "warn",
  partial: "warn",
  degraded: "bad",
  // jobs.state and job_items.state — the same five words plus `cancelled`,
  // which is not a failure and must not be coloured as one.
  queued: "wait",
  cancelled: "neutral",
  // job_events.level
  warn: "warn",
  error: "bad",
  info: "neutral",
  debug: "neutral",
  // follows.state — what the clock is doing, not what a video is doing.
  active: "ok",
  paused: "wait",
  failing: "bad",
  // follow_seen.decision
  held_budget: "warn",
  held_review: "warn",
  already_indexed: "ok",
  skipped_tab: "wait",
  skipped_title: "wait",
  skipped_duration: "wait",
  skipped_horizon: "wait",
};

/** A tone for a state word, or `neutral` for anything unrecognised. */
export function toneOf(state: string | null | undefined): Tone {
  return TONES[(state ?? "").trim().toLowerCase()] ?? "neutral";
}

/**
 * `tone` is the escape hatch for the handful of states whose *word* is not in
 * any of the store's vocabularies because the page is naming a condition rather
 * than reading a column — "full-text only" for search without its vector legs,
 * "allowed" and "refused" for indexing. The word is still the message; only the
 * lookup is bypassed.
 */
export function Pill({ state, tone }: { state: string; tone?: Tone }) {
  return <span className={`${styles.pill} ${styles[tone ?? toneOf(state)]}`}>{state}</span>;
}
