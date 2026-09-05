"use client";

import { useEffect, useState } from "react";
import { Pill } from "@/components/Pill";
import type { JobCard } from "@/lib/dashboard/schemas";
import { count, duration } from "@/lib/format";
import styles from "./jobs.module.css";

// What the two jobs pages are built from. Every value below is composed here,
// from the payload's typed fields: the `text` block that once carried
// `progress`, `counts`, `tally`, `wall` and the rest was rendered for a script
// with no formatter of its own, and it went with that script on 2026-09-06
// (frontend-migration.md §1 decision 5, dashboard.md §23).
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
          {job.basis ? <span className={styles.hintLine}>{job.basis}</span> : null}
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

// The three things a write control needs — `useWrite`, `refusalOf` and
// `useWriteSide` — were here while the jobs pages were the only pages that
// wrote. The following pages write too, and two transcriptions of one control
// is how two pages on one surface start behaving like two surfaces, so they
// live in `../parts.tsx` now, where that module's own comment says the shared
// vocabulary goes.
