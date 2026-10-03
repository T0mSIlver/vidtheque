"use client";

import { RetryIn } from "@/components/ui/RetryIn";
import { refusalOf } from "@/components/dashboard/kit/write";
import { useSession } from "@/components/dashboard/session";
import { DashboardError, navigation } from "@/lib/dashboard/client";
import type { Match } from "@/lib/dashboard/schemas";
import { scoreWord } from "@/lib/feed/words";
import styles from "./feed.module.css";

/** The score as a figure and its word; 3 is the one in gold. */
export function Score({ score }: { score: number }) {
  return (
    <span className={styles.score} data-score={score}>
      <b className={styles.scoreFigure}>{score}</b>
      <span className={styles.scoreWord}>{scoreWord(score)}</span>
    </span>
  );
}

/** Exploration's verdicts (companion.md §3.2), said as a word. */
export function Outside() {
  return <span className={styles.outside}>outside your profile</span>;
}

/** The profile entries a verdict hit, as the app draws them: green for more of
 *  this, red for less, one chevron when the video touches it, two when it is
 *  central. The chevron carries the direction, so colour is never alone. */
export function Matches({ matches }: { matches: Match[] }) {
  if (matches.length === 0) return null;
  return (
    <ul className={styles.matches} aria-label="Profile matches">
      {matches.map((match) => (
        <li
          key={match.entry_id}
          className={styles.match}
          data-direction={match.direction}
          aria-label={`${match.strength >= 2 ? "Strongly matches" : "Matches"} ${
            match.direction === "up" ? "an interest" : "something you avoid"
          }: ${match.text}`}
        >
          <Chevrons up={match.direction === "up"} double={match.strength >= 2} />
          <span>{match.text}</span>
        </li>
      ))}
    </ul>
  );
}

function Chevrons({ up, double }: { up: boolean; double: boolean }) {
  // Drawn pointing up; a down match turns it over.
  return (
    <svg
      className={styles.chevrons}
      viewBox="0 0 16 16"
      aria-hidden="true"
      style={up ? undefined : { transform: "rotate(180deg)" }}
    >
      <polyline points={double ? "4,8 8,4 12,8" : "4,10 8,6 12,10"} />
      {double ? <polyline points="4,12 8,8 12,12" /> : null}
    </svg>
  );
}

/** A read that failed: the limiter's countdown, the way to sign in, or the
 *  refusal in the API's words with a retry. */
export function FeedFailure({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const session = useSession();
  if (error instanceof DashboardError && error.status === 429) {
    return (
      <RetryIn
        seconds={error.retryAfter ?? 60}
        message={error.message || "Too many requests for now."}
        onRetry={onRetry}
      />
    );
  }
  if (error instanceof DashboardError && error.status === 401) {
    const login = session?.login_url;
    return (
      <section className={styles.failure} role="alert">
        <h1 className={styles.failureTitle}>Sign in to read your feed.</h1>
        {login ? (
          <a
            className={styles.action}
            href={`${login}?next=${encodeURIComponent(navigation.path())}`}
          >
            Sign in
          </a>
        ) : null}
      </section>
    );
  }
  const refusal = refusalOf(error);
  return (
    <section className={styles.failure} role="alert">
      <h1 className={styles.failureTitle}>{refusal.message}</h1>
      <p className={styles.code}>{refusal.code}</p>
      {refusal.next ? <p className={styles.quiet}>{refusal.next}</p> : null}
      <button className={styles.action} type="button" onClick={onRetry}>
        Try again
      </button>
    </section>
  );
}
