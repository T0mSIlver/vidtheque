"use client";

import { Pill } from "@/components/ui/Pill";
import { useTicking } from "@/components/ui/RetryIn";
import type { JobCard } from "@/lib/dashboard/schemas";
import { count, duration } from "@/lib/format";
import styles from "./jobs.module.css";

// What the two jobs pages are built from, composed from the payload's numbers
// (dashboard.md §23); `basis` is the one sentence read rather than composed.

const MIN_TICK_MS = 1_000;
const FALLBACK_TICK_MS = 2_000;

/** The tick: the payload's own cadence, floored, while anything is live
 *  (dashboard.md §5.4 — polling, not SSE). */
export function livePoll(data: { live: boolean; poll_ms: number }): number | null {
  return data.live ? Math.max(MIN_TICK_MS, data.poll_ms || FALLBACK_TICK_MS) : null;
}

/** All five buckets, zeroes included, so the tally visibly adds up. */
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

/** `1/2 done · 1 failed` */
export function countsOf(job: JobCard): string {
  const parts = [`${job.n_done}/${job.n_items} done`];
  if (job.n_failed) parts.push(`${job.n_failed} failed`);
  if (job.n_skipped) parts.push(`${job.n_skipped} skipped`);
  if (job.n_cancelled) parts.push(`${job.n_cancelled} cancelled`);
  return parts.join(" · ");
}

/** The bar and its figure. The width animates over one poll interval, and
 *  only a running job draws the working edge. */
export function Progress({ job, tickMs, wide }: { job: JobCard; tickMs: number; wide?: boolean }) {
  const hintId = `pct-${job.job_id}`;
  return (
    <>
      <span
        className={[
          styles.meter,
          wide ? styles.meterWide : "",
          job.state === "running" ? styles.working : "",
        ]
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

/** `held 4m 00s more`, and nothing once the wait is over. */
export function Held({ seconds, moving = true }: { seconds: number; moving?: boolean }) {
  const left = useTicking(seconds > 0 ? seconds : null, moving, -1);
  if (left === null || left <= 0) return null;
  return (
    <span className={styles.countdown}>
      held <span>{duration(left)}</span> more
    </span>
  );
}

/** A live job's wall clock ticks up; a finished one is a measurement. */
export function WallClock({ seconds, live }: { seconds: number | null; live: boolean }) {
  return <>{duration(useTicking(seconds, live, 1))}</>;
}

/** The state word, a pending cancel, the code that set a wait, the countdown.
 *  `codeFirst` is the detail page's order (§5.4: the countdown "with
 *  `error_code` beside it"); `moving` stops the clock with the poll. */
export function JobStates({
  job,
  codeFirst,
  moving = true,
}: {
  job: JobCard;
  codeFirst?: boolean;
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

/** The first item's title, or the payload's sentence for a job with nothing
 *  fetched yet — muted, because it is a count standing in for a name. */
export function jobHeadline(job: JobCard): { text: string; muted: boolean } {
  const title = job.contents?.title;
  if (title) return { text: title, muted: false };
  const note = job.contents?.note;
  return { text: note ?? `${count(job.n_items)} item(s), none fetched yet`, muted: true };
}
