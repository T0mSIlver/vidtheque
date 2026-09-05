"use client";

import { useCallback, useEffect, useState } from "react";
import { Pill } from "@/components/Pill";
import { DashboardError } from "@/lib/dashboard/client";
import type { JobCard } from "@/lib/dashboard/schemas";
import { count, duration } from "@/lib/format";
import { useSession } from "../session";
import styles from "./jobs.module.css";

// What the two jobs pages are built from. Every piece is on the Jinja pages
// that still serve the rest of this surface, and every value below is computed
// from the payload's *typed* half: `text.progress`, `text.counts`,
// `text.tally`, `text.wall` and the rest are the same numbers already
// rendered, for a script that carries no formatter, and this side has one
// (frontend-migration.md §1 decision 5).
//
// `basis` is the single exception and is read, not composed: it is the
// sentence saying what the percentage is *computed over*, which is policy and
// stays Python's.

/** The three-word tally the percentage is made of, from the five counts.
 *
 *  All five buckets, always, including the zeroes — the point of the line is
 *  that they add up to `n_items`, and a tally with terms missing does not
 *  visibly add up. `still to run` is the remainder, which is the one term the
 *  payload does not carry as a field of its own. */
export function tallyOf(job: JobCard): string {
  const pending = Math.max(
    0,
    job.n_items - job.n_done - job.n_failed - job.n_skipped - job.n_cancelled,
  );
  return [
    `${job.n_done} done`,
    `${job.n_failed} failed`,
    `${job.n_skipped} skipped`,
    `${job.n_cancelled} cancelled`,
    `${pending} still to run`,
  ].join(" · ");
}

/** `1/2 done · 1 failed` — the items column, and the job header's third fact. */
export function countsOf(job: JobCard): string {
  const parts = [`${job.n_done}/${job.n_items} done`];
  if (job.n_failed) parts.push(`${job.n_failed} failed`);
  if (job.n_skipped) parts.push(`${job.n_skipped} skipped`);
  if (job.n_cancelled) parts.push(`${job.n_cancelled} cancelled`);
  return parts.join(" · ");
}

/** The sentence that says what the percentage is computed over.
 *
 *  Read from beside the typed fields first and from inside the `text` block
 *  second, because it moves: §5.4 keeps `basis` when the rest of `text` is
 *  deleted with `static/jobs.js`, "in `notes` or beside it". Both readings are
 *  optional, so this shell renders against the instance that has already made
 *  that move and against the one that has not. */
export function basisOf(job: JobCard): string | null {
  return job.basis ?? job.text?.basis ?? null;
}

/**
 * The bar, and the figure with its breakdown behind it.
 *
 * `tickMs` is the poll interval the payload named, and it is what the width
 * animates over: the cadence is the server's, so the movement is a measurement
 * rather than a chosen duration. `working` is `running` and nothing else —
 * queued, deferred, finished and failed all draw a bar that does not move,
 * because none of them is a machine doing anything.
 */
export function Progress({ job, tickMs, wide }: { job: JobCard; tickMs: number; wide?: boolean }) {
  const working = job.state === "running";
  const basis = basisOf(job);
  const hintId = `pct-${job.job_id}`;
  return (
    <>
      <span
        className={[styles.meter, wide ? styles.meterWide : "", working ? styles.working : ""]
          .filter(Boolean)
          .join(" ")}
        aria-hidden="true"
      >
        <span
          className={styles.meterFill}
          style={{ width: `${job.progress}%`, transitionDuration: `${tickMs}ms` }}
        />
      </span>
      <span className={styles.hinted}>
        <span className={styles.meterLabel} tabIndex={0} aria-describedby={hintId}>
          {job.progress}%
        </span>
        <span className={styles.hint} role="tooltip" id={hintId}>
          <span className={styles.hintLine}>{tallyOf(job)}</span>
          {basis ? <span className={styles.hintLine}>{basis}</span> : null}
        </span>
      </span>
    </>
  );
}

/**
 * A number of seconds the server sent, counting on between readings.
 *
 * The two clocks that move on these pages are arithmetic on numbers the last
 * payload carried — a deferral counting down to zero, and a live job's wall
 * clock counting up — so both are re-seeded by every poll and neither can
 * drift. A finished job's wall clock is a measurement and does not tick: a
 * measurement that keeps counting is a lie.
 *
 * State is adjusted during render rather than in an effect, which is React's
 * own answer to "reset when a prop changes": an effect that sets state on
 * arrival paints the stale number first.
 */
function useTicking(seconds: number | null, moving: boolean, step: 1 | -1): number | null {
  const [sent, setSent] = useState(seconds);
  const [now, setNow] = useState(seconds);
  if (sent !== seconds) {
    setSent(seconds);
    setNow(seconds);
  }

  useEffect(() => {
    if (!moving || seconds === null) return;
    const id = setInterval(() => setNow((value) => Math.max(0, (value ?? 0) + step)), 1000);
    return () => clearInterval(id);
  }, [moving, seconds, step]);

  return now;
}

/** `held 4m 00s more`, on a job whose `not_before` is still in the future.
 *
 *  Absent rather than zeroed when the wait is over: a countdown showing `0s`
 *  is a wait that is not happening. */
export function Held({ seconds }: { seconds: number }) {
  const left = useTicking(seconds > 0 ? seconds : null, true, -1);
  if (left === null || left <= 0) return null;
  return (
    <span className={styles.countdown}>
      held <span>{duration(left)}</span> more
    </span>
  );
}

/** A live job's wall clock, ticking up; a finished one's, standing still. */
export function WallClock({ seconds, live }: { seconds: number | null; live: boolean }) {
  const value = useTicking(seconds, live, 1);
  return <>{duration(value)}</>;
}

/** The state cell: the word, what the request did to it, and what set the wait.
 *
 *  `cancel requested` shows only while the job is still live — on a settled job
 *  the state word is already `cancelled` and the second badge would be the row
 *  saying the same thing twice. */
export function JobStates({ job }: { job: JobCard }) {
  return (
    <>
      <Pill state={job.state} />
      {job.cancel_requested && job.live ? <Pill state="cancel requested" tone="warn" /> : null}
      {job.error_code ? <Pill state={job.error_code} tone="bad" /> : null}
      <Held seconds={job.defer_s} />
    </>
  );
}

/** `2` when the job holds two items and nothing has resolved yet — what the row
 *  can say about its contents when the payload does not carry a title. */
export function jobHeadline(job: JobCard): string {
  const title = job.contents?.title;
  if (title) return title;
  return `${count(job.n_items)} item(s)`;
}

// ------------------------------------------------------------- the writes

/** One write, in the four states a control has to be able to draw. */
export type Write<T> =
  | { status: "idle" }
  | { status: "sending" }
  | { status: "done"; outcome: T }
  | { status: "failed"; error: unknown };

/**
 * A control that POSTs once and shows what came back.
 *
 * The outcome is rendered inline because that is the reason these routes
 * answer inline at all: a cancel whose only evidence is the next 2 s tick is a
 * button that looks broken, and the state the job is *now* in — settled
 * `cancelled`, or `running` with the request recorded — is precisely what a
 * poll cannot tell an operator (dashboard.md §21).
 *
 * `onDone` is how the page brings the tick back: a job that was terminal has a
 * stopped poll, and a retry has just made something live again.
 */
export function useWrite<T>(send: () => Promise<T>, onDone?: (outcome: T) => void) {
  const [state, setState] = useState<Write<T>>({ status: "idle" });

  const run = useCallback(() => {
    setState({ status: "sending" });
    send().then(
      (outcome) => {
        setState({ status: "done", outcome });
        onDone?.(outcome);
      },
      (error: unknown) => setState({ status: "failed", error }),
    );
  }, [send, onDone]);

  return [state, run] as const;
}

/** Why a write was refused, in the API's own words. Code, message and the
 *  `next:` line are policy text and stay Python's; `403 E_BAD_ORIGIN` is the
 *  one that is a bug on this side rather than in the reader's session. */
export function refusalOf(error: unknown): { code: string; message: string; next?: string } {
  if (error instanceof DashboardError) {
    return { code: error.code, message: error.message, next: error.next };
  }
  return {
    code: "E_UNREACHABLE",
    message: error instanceof Error ? error.message : "The write did not reach the instance.",
  };
}

/**
 * Should this deployment draw a write control at all?
 *
 * `write_side` is whether the routes are registered — in
 * `VIDTHEQUE_PUBLIC_READONLY=1` and `VIDTHEQUE_AUTH=none` they are not, so
 * there is **no control**, disabled or otherwise: a button that 404s is worse
 * than a button that is not there (dashboard.md §2.3, §3.2 rule 3, §18.1).
 *
 * `writes_allowed` is the database's own flag and is a different question, so
 * it disables rather than removes — and only the controls that feed
 * `index_video`. Cancel writes the job row, not the index, and stays live
 * exactly when an operator most needs it to (§5.5, and `job.html`'s own rule).
 */
export function useWriteSide(): { rendered: boolean; indexable: boolean } {
  const session = useSession();
  return {
    rendered: Boolean(session?.write_side),
    indexable: Boolean(session?.writes_allowed),
  };
}
