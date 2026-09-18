import { useEffect, useState } from "react";
import type { AskFailure, EditionTalk } from "@/lib/api/schemas";
import { Answer } from "./AskAnswer";
import type { AskPhase, Line } from "./requests";
import { InPlaceLink } from "./SearchResults";
import styles from "./console.module.css";

/**
 * The answer pane: a live region, busy until the answer so a screen reader
 * does not narrate each activity row twice (demo-site.md §6.6).
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
  return (
    <section className={styles.answer} aria-live="polite" aria-busy={busy} aria-label="Answer">
      {busy ? <WorkLog lines={phase.lines} live /> : null}
      {phase.kind === "answered" ? (
        <Answer answer={phase.answer} talks={talks}>
          <WorkDisclosure lines={phase.lines} />
        </Answer>
      ) : null}
      {phase.kind === "degraded" ? (
        <Degraded
          body={phase.body}
          canRetry={Boolean(draft.trim())}
          searchHref={searchHref}
          onRetry={onRetry}
          onSearch={onSearch}
        >
          <WorkDisclosure lines={phase.lines} />
        </Degraded>
      ) : null}
    </section>
  );
}

// How many finished steps stay on screen while the model works.
const RECENT = 5;

/**
 * One row per tool call. Live, it is a fixed six-row block — the last few steps
 * and the one in flight — so the page under it never moves and nothing scrolls:
 * a new row grows in, the oldest folds away. Folded under an answer, it is the
 * whole list, still.
 */
function WorkLog({ lines, live = false }: { lines: Line[]; live?: boolean }) {
  const inFlight = lines.some((l) => l.result === undefined);
  const hidden = live ? Math.max(0, lines.length - RECENT) : 0;
  return (
    <div className={live ? styles.work : styles.workFolded}>
      {live ? (
        <p className={styles.workHead}>
          <span>Reading the corpus</span>
          {lines.length > 0 ? (
            <span>{`${lines.length} step${lines.length === 1 ? "" : "s"}`}</span>
          ) : null}
        </p>
      ) : null}
      <ol className={styles.steps} aria-label="What the model is doing">
        {lines.map((l, i) => (
          <li
            key={l.id}
            className={[
              styles.step,
              live && l.result === undefined ? styles.stepLive : "",
              i < hidden ? styles.stepGone : "",
            ]
              .filter(Boolean)
              .join(" ")}
          >
            <span className={styles.stepText}>{l.text}</span>
          </li>
        ))}
        {/* Between tool calls the model is deciding what to read next. */}
        {live ? (
          <li
            className={`${styles.step} ${styles.stepLive} ${inFlight ? styles.stepGone : ""}`}
            aria-hidden={inFlight}
          >
            <span className={styles.stepText}>Thinking</span>
          </li>
        ) : null}
      </ol>
    </div>
  );
}

function WorkDisclosure({ lines }: { lines: Line[] }) {
  if (!lines.length) return null;
  return (
    <details className={styles.disclosure}>
      <summary>Show its work</summary>
      <WorkLog lines={lines} />
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
  children,
}: {
  body: AskFailure;
  canRetry: boolean;
  searchHref: string;
  onRetry: () => void;
  onSearch: () => void;
  children: React.ReactNode;
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
      {children}
    </div>
  );
}
