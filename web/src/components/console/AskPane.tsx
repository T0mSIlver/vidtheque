import { useEffect, useRef, useState } from "react";
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
      {busy ? (
        <>
          <WorkLog lines={phase.lines} />
          {/* Parked in the flow, not removed, so the document keeps its height
              while a tool call owns the caret. */}
          <p
            className={`${styles.thinking} ${
              phase.lines.some((l) => l.result === undefined) ? styles.parked : ""
            }`}
          >
            reading the corpus…
          </p>
        </>
      ) : null}
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

// A fixed six-line box that scrolls itself to the newest line, so the page
// under it never grows a row per tool call.
function WorkLog({ lines, folded = false }: { lines: Line[]; folded?: boolean }) {
  const list = useRef<HTMLOListElement>(null);
  useEffect(() => {
    if (list.current) list.current.scrollTop = list.current.scrollHeight;
  }, [lines.length]);

  return (
    <ol
      ref={list}
      className={`${styles.log} ${folded ? styles.logFolded : ""}`}
      aria-label="What the model is doing"
    >
      {lines.map((l) => (
        <li key={l.id} className={l.result === undefined ? styles.running : undefined}>
          <span className={styles.logText}>{l.text}</span>
          {l.result ? <span className={styles.logResult}>{` → ${l.result}`}</span> : null}
        </li>
      ))}
    </ol>
  );
}

function WorkDisclosure({ lines }: { lines: Line[] }) {
  if (!lines.length) return null;
  return (
    <details className={styles.disclosure}>
      <summary>Show its work</summary>
      <WorkLog lines={lines} folded />
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
