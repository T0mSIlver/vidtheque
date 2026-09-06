"use client";

import { useEffect, useState } from "react";
import { Pill } from "@/components/Pill";
import type { JobCard, Jobs } from "@/lib/dashboard/schemas";
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
export function useTicking(seconds: number | null, moving: boolean, step: 1 | -1): number | null {
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
export function Held({ seconds, moving = true }: { seconds: number; moving?: boolean }) {
  const left = useTicking(seconds > 0 ? seconds : null, moving, -1);
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
export function JobStates({
  job,
  codeFirst,
  moving = true,
}: {
  job: JobCard;
  /** `job.html`'s order — the code that set the wait, then the request that has
   *  not landed yet. §5.4 asks for the countdown "with `error_code` beside it",
   *  and on a deferred job the code is the half that explains the clock. The
   *  table draws the other order, because there the request is the column's
   *  news and the code is a badge it already carries. */
  codeFirst?: boolean;
  /** Does the second-hand still move? It stops with the poll: a countdown that
   *  keeps running against a reading nothing is refreshing is a wait the page
   *  is inventing. */
  moving?: boolean;
}) {
  const requested =
    job.cancel_requested && job.live ? <Pill state="cancel requested" tone="warn" /> : null;
  const code = job.error_code ? <Pill state={job.error_code} tone="bad" /> : null;
  return (
    <>
      <Pill state={job.state} />
      {codeFirst ? code : requested}
      {codeFirst ? requested : code}
      <Held seconds={job.defer_s} moving={moving} />
    </>
  );
}

/** What the row calls the job: the first item's video title, or — while the
 *  items have not been fetched — the sentence `job_contents` writes instead.
 *
 *  `muted` is half the reading. A job whose items have not resolved yet has no
 *  title to print, and "2 item(s), none fetched yet" set at the weight of a
 *  name would read as one: the tone is what says this is a count standing in
 *  for a title. The sentence is the payload's, not this side's — the fallback
 *  is only for an instance whose `contents` block predates it. */
export function jobHeadline(job: JobCard): { text: string; muted: boolean } {
  const title = job.contents?.title;
  if (title) return { text: title, muted: false };
  const note = job.contents?.note;
  return { text: note ?? `${count(job.n_items)} item(s), none fetched yet`, muted: true };
}

/**
 * The rows the tick patches — the ones that were on the page when it loaded.
 *
 * `jobs.js` patched the rows it could find and revealed a note for the ones it
 * could not: a job queued *since* this page rendered has no row to patch, and a
 * table that silently grows a row under the reader's cursor is a table whose
 * count line has stopped being true. React would happily re-render the whole
 * body every two seconds instead; this keeps the original's contract, which is
 * also the one that keeps a row's identity — the focus inside a cell, a
 * selection, an open hint — across a reading.
 *
 * A job that drops out of the listing keeps its last reading rather than
 * vanishing mid-triage, which is what patching in place meant.
 */
export function usePatchedRows(data: Jobs): { rows: JobCard[]; queuedSince: boolean } {
  // The baseline is fixed at the first reading; the rows are patched by every
  // one after it. Adjusted during render rather than in an effect, which is
  // React's own answer to "recompute when a prop changes": an effect would
  // paint the previous payload's rows first.
  const [order] = useState(() => data.jobs.map((job) => job.job_id));
  const [seen, setSeen] = useState<Jobs | null>(null);
  const [rows, setRows] = useState<JobCard[]>(data.jobs);
  const [queuedSince, setQueuedSince] = useState(false);

  if (seen !== data) {
    setSeen(data);
    const arrived = new Map(data.jobs.map((job) => [job.job_id, job]));
    const held = new Map(rows.map((row) => [row.job_id, row]));
    setRows(
      order
        .map((id) => arrived.get(id) ?? held.get(id))
        .filter((job): job is JobCard => job !== undefined),
    );
    const known = new Set(order);
    if (data.jobs.some((job) => !known.has(job.job_id))) setQueuedSince(true);
  }

  return { rows, queuedSince };
}

// The three things a write control needs — `useWrite`, `refusalOf` and
// `useWriteSide` — were here while the jobs pages were the only pages that
// wrote. The following pages write too, and two transcriptions of one control
// is how two pages on one surface start behaving like two surfaces, so they
// live in `../parts.tsx` now, where that module's own comment says the shared
// vocabulary goes.
