import { useEffect, useRef, useState } from "react";
import type { AskFailure, EditionTalk } from "@/lib/api/schemas";
import { Answer } from "./AskAnswer";
import type { AskPhase, Line } from "./requests";
import { InPlaceLink } from "./SearchResults";
import styles from "./console.module.css";

/**
 * The answer pane: how the answer was found, folded to one line above it, then
 * the answer. A live region, busy until the answer so a screen reader does not
 * narrate each step twice (demo-site.md §6.6).
 */
export function AskPane({
  phase,
  draft,
  talks,
  searchHref,
  onRetry,
  onSearch,
}: {
  phase: Exclude<AskPhase, { kind: "idle" }>;
  draft: string;
  talks: EditionTalk[];
  searchHref: string;
  onRetry: () => void;
  onSearch: () => void;
}) {
  const busy = phase.kind === "working";
  // The pane mounts with the ask (it is keyed by it), so this is when it began.
  const began = useRef(0);
  const [took, setTook] = useState<number | null>(null);
  useEffect(() => {
    began.current = performance.now();
  }, []);
  useEffect(() => {
    if (!busy) setTook(Math.max(1, Math.round((performance.now() - began.current) / 1000)));
  }, [busy]);

  return (
    <section className={styles.answer} aria-live="polite" aria-busy={busy} aria-label="Answer">
      <Work lines={phase.lines} live={busy} took={took} />
      {phase.kind === "answered" ? <Answer answer={phase.answer} talks={talks} /> : null}
      {phase.kind === "degraded" ? (
        <Degraded
          body={phase.body}
          canRetry={Boolean(draft.trim())}
          searchHref={searchHref}
          onRetry={onRetry}
          onSearch={onSearch}
        />
      ) : null}
    </section>
  );
}

type Tick = { key: string; text: string };

// Between tool calls the model is deciding what to read next.
function thinkingAfter(id: number): Tick {
  return { key: `think-${id}`, text: "Thinking" };
}

/** The one line the folded work shows while the model works, and the line it replaced. */
function ticks(lines: Line[]): { now: Tick; before: Tick | null } {
  const last = lines.at(-1);
  if (!last) return { now: thinkingAfter(0), before: null };
  if (last.result === undefined) {
    return {
      now: { key: `step-${last.id}`, text: last.text },
      before: thinkingAfter(lines.at(-2)?.id ?? 0),
    };
  }
  return { now: thinkingAfter(last.id), before: { key: `step-${last.id}`, text: last.text } };
}

/**
 * How the answer was found, as a disclosure folded to one line. Live, the line
 * is the step in flight, and each new step replaces the last; open, it is every
 * step so far. Once the answer lands the line says how long it took.
 */
function Work({ lines, live, took }: { lines: Line[]; live: boolean; took: number | null }) {
  const [open, setOpen] = useState(false);
  if (!live && !lines.length) return null;
  const { now, before } = ticks(lines);
  const count = `${lines.length} step${lines.length === 1 ? "" : "s"}`;
  return (
    <details
      className={`${styles.work} ${live ? styles.workLive : ""}`}
      open={open}
      onToggle={(e) => setOpen(e.currentTarget.open)}
    >
      <summary className={styles.workLine}>
        <span className={styles.chevron} aria-hidden>
          <svg viewBox="0 0 12 12">
            <path
              d="M3.6 1.8 L8.2 6 L3.6 10.2"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
            />
          </svg>
        </span>
        {live ? (
          <span className={styles.ticker}>
            {before ? (
              <span key={before.key} className={styles.tickOut} aria-hidden>
                {before.text}
              </span>
            ) : null}
            <span key={now.key} className={`${styles.tickIn} ${styles.lit}`}>
              {now.text}
            </span>
          </span>
        ) : (
          <span className={styles.ticker}>
            <span>{took ? `Worked for ${took} s` : "Show its work"}</span>
          </span>
        )}
        {lines.length ? <span className={styles.workCount}>{count}</span> : null}
      </summary>
      <ol className={styles.steps} aria-label="What the model did">
        {lines.map((l) => (
          <li
            key={l.id}
            className={`${styles.step} ${live && l.result === undefined ? styles.stepLive : ""}`}
          >
            <span className={live && l.result === undefined ? styles.lit : undefined}>
              {l.text}
            </span>
          </li>
        ))}
        {live && now.key.startsWith("think") ? (
          <li className={`${styles.step} ${styles.stepLive}`}>
            <span className={styles.lit}>Thinking</span>
          </li>
        ) : null}
      </ol>
    </details>
  );
}

/** `retry_after_s` is a float: floor of one second, ceiling of the fraction. */
function useCountdown(seconds: number | null | undefined): number {
  const [left, setLeft] = useState(() =>
    seconds ? Math.max(1, Math.ceil(Number(seconds) || 1)) : 0,
  );
  useEffect(() => {
    if (left <= 0) return;
    const id = setTimeout(() => setLeft(left - 1), 1000);
    return () => clearTimeout(id);
  }, [left]);
  return left;
}

// Search always works, so a refused ask offers it; the retry stays gated until
// the wait is over, because a retry into a refusal is one more refusal.
function Degraded({
  body,
  canRetry,
  searchHref,
  onRetry,
  onSearch,
}: {
  body: AskFailure;
  canRetry: boolean;
  searchHref: string;
  onRetry: () => void;
  onSearch: () => void;
}) {
  const left = useCountdown(body.retry_after_s);
  return (
    <div className={styles.degraded} role="status">
      <p className={styles.degradedMessage}>{body.message}</p>
      {body.retry_after_s ? (
        <p className={styles.wait}>{left > 0 ? `Try again in ${left}s.` : "Try again."}</p>
      ) : null}
      <p className={styles.degradedActions}>
        <button
          type="button"
          onClick={onRetry}
          disabled={!canRetry || left > 0}
          className={styles.ghost}
        >
          Try again
        </button>{" "}
        <InPlaceLink className={styles.ghost} href={searchHref} onRun={onSearch}>
          Search instead
        </InPlaceLink>
      </p>
    </div>
  );
}
