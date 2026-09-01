import styles from "./Pill.module.css";

// Every state prints its own word, in its tone. Colour reinforces; the word
// is the message (DESIGN.md, the Word-and-Colour Rule).
const TONES: Record<string, keyof typeof styles> = {
  ready: "ok",
  ok: "ok",
  pending: "wait",
  queued: "wait",
  running: "work",
  degraded: "warn",
  partial: "warn",
  failed: "bad",
};

export function Pill({ state }: { state: string }) {
  const tone = TONES[state] ?? "neutral";
  return <span className={`${styles.pill} ${styles[tone]}`}>{state}</span>;
}
