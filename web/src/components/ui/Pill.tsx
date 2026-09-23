import styles from "./Pill.module.css";

// A state prints its own word in its tone; colour reinforces (DESIGN.md, the
// Word-and-Colour Rule). The map is from the store's words to a tone, and an
// unrecognised word renders neutral rather than disappearing.
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
  // data_status
  ok: "ok",
  no_transcript: "warn",
  no_ocr: "warn",
  no_frames: "warn",
  partial: "warn",
  degraded: "bad",
  // jobs.state: `cancelled` is not a failure
  queued: "wait",
  cancelled: "neutral",
  // job_events.level
  warn: "warn",
  error: "bad",
  info: "neutral",
  debug: "neutral",
  // follows.state
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
function toneOf(state: string | null | undefined): Tone {
  return TONES[(state ?? "").trim().toLowerCase()] ?? "neutral";
}

/** `tone` bypasses the lookup for a word that names a condition rather than a
 *  column value ("full-text only", "refused"). */
export function Pill({ state, tone }: { state: string; tone?: Tone }) {
  const resolved = tone ?? toneOf(state);
  return (
    <span className={`${styles.pill} ${styles[resolved]}`} data-tone={resolved}>
      {state}
    </span>
  );
}
