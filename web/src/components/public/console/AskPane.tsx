import { useEffect, useLayoutEffect, useRef, useState } from "react";
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
  // Layout effects, so the line never paints a frame without its duration.
  const began = useRef(0);
  const [took, setTook] = useState<number | null>(null);
  useLayoutEffect(() => {
    began.current = performance.now();
  }, []);
  useLayoutEffect(() => {
    if (!busy) setTook(Math.max(1, Math.round((performance.now() - began.current) / 1000)));
  }, [busy]);

  return (
    <section className={styles.answer} aria-live="polite" aria-busy={busy} aria-label="Answer">
      <Work
        lines={phase.lines}
        live={busy}
        took={phase.kind === "answered" && phase.took ? phase.took : took}
      />
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

/** What the work is doing right now — the truth, before the ticker paces it. */
function tickOf(lines: Line[]): Tick {
  const last = lines.at(-1);
  if (!last) return thinkingAfter(0);
  if (last.result === undefined) return { key: `step-${last.id}`, text: last.text };
  return thinkingAfter(last.id);
}

// How long a line holds the summary before the next one may take it. A search
// answers in a few hundred milliseconds, so the truth flips back to "Thinking"
// before the eye has read what was searched for, and the line spends the run
// saying the one thing that carries no information (Tom, 2026-09-20). A step is
// held long enough to read; the filler between steps is not.
const STEP_MS = 2000;
const THINK_MS = 600;

function dwell(tick: Tick): number {
  return tick.key.startsWith("think") ? THINK_MS : STEP_MS;
}

/** The next line to show, dropping filler when the model is outrunning the eye. */
function take(queued: Tick[]): Tick | undefined {
  while (queued.length > 1 && queued[0].key.startsWith("think")) queued.shift();
  return queued.shift();
}

/**
 * The paced line, and the line it replaced. The pane reads its steps at the
 * speed they arrive; this is the speed they can be read at (demo-site.md §6.6).
 */
function useTicker(key: string, text: string, live: boolean): { now: Tick; before: Tick | null } {
  const [shown, setShown] = useState<{ now: Tick; before: Tick | null }>({
    now: { key, text },
    before: null,
  });
  const queued = useRef<Tick[]>([]);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const since = useRef(0);

  useEffect(() => {
    if (!live) {
      queued.current = [];
      if (timer.current) clearTimeout(timer.current);
      timer.current = null;
      return;
    }
    const tail = queued.current.at(-1) ?? shown.now;
    if (key === tail.key) return;
    queued.current.push({ key, text });
    if (timer.current) return;
    const pump = () => {
      const next = take(queued.current);
      if (!next) {
        timer.current = null;
        return;
      }
      since.current = performance.now();
      setShown((was) => ({ now: next, before: was.now }));
      timer.current = setTimeout(pump, dwell(next));
    };
    // Whatever is left of the line on screen, so a step is never cut short and
    // a line that has had its time is replaced on the next frame.
    const left = Math.max(0, dwell(shown.now) - (performance.now() - since.current));
    timer.current = setTimeout(pump, left);
  }, [key, text, live, shown.now]);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  return shown;
}

/**
 * How the answer was found, as a disclosure folded to one line. Live, the line
 * is the step in flight, and each new step replaces the last; open, it is every
 * step so far. Once the answer lands the line says how long it took.
 */
function Work({ lines, live, took }: { lines: Line[]; live: boolean; took: number | null }) {
  const [open, setOpen] = useState(false);
  const tick = tickOf(lines);
  const { now, before } = useTicker(tick.key, tick.text, live);
  if (!live && !lines.length) return null;
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
