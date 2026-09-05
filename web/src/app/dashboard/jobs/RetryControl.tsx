"use client";

import { useCallback } from "react";
import { dashboard, ROOT } from "@/lib/dashboard/client";
import type { JobCard, RetryOutcome } from "@/lib/dashboard/schemas";
import dash from "../dashboard.module.css";
import { DashLink, Sep } from "../parts";
import styles from "./jobs.module.css";
import { refusalOf, useWrite, useWriteSide } from "./parts";

// Retry the failed and degraded items of a finished job —
// `POST /dashboard/jobs/{id}/retry` (dashboard.md §16.2, §21).
//
// The condition is `job.html`'s, exactly: the job has finished, it is an
// indexing job (`index` or `reindex` — a `delete` or a `follow_check` has
// nothing `index_video` could repair), and something in it needs repairing —
// a failed item, or a `done` item with a failed stage underneath it. Successful
// items are deliberately not re-queued, which is why the count on the button is
// the two failures added rather than the job's size.
//
// It is *disabled* rather than absent when the database refuses writes, which
// is the one difference from Cancel beside it: this feeds `index_video`, and
// cancel writes the job row rather than the index. Whether either is drawn at
// all is the deployment's answer (`write_side`), and both pages ask first.
//
// The receipt is read from the response rather than followed: the page goes
// straight to the new job when there is exactly one, and a client that always
// reads `jobs` has no special case. A `409` is a receipt too — it means every
// batch was refused, and the refusals are on it.

/** Does this job offer the action at all? `job.html`'s own predicate. */
export function retryable(job: JobCard): boolean {
  return (
    !job.live && ["index", "reindex"].includes(job.kind) && Boolean(job.n_failed || job.degraded)
  );
}

export function RetryControl({ job }: { job: JobCard }) {
  const { indexable } = useWriteSide();
  const send = useCallback(() => dashboard.retryJob(job.job_id), [job.job_id]);
  const [write, run] = useWrite(send);

  if (write.status === "done") return <Receipt outcome={write.outcome} />;

  if (write.status === "failed") {
    const refusal = refusalOf(write.error);
    return (
      <div className={styles.receipt} role="status">
        <p className={`${styles.receiptLine} ${styles.outcomeBad}`}>
          <code>{refusal.code}</code>
          <span>{refusal.message}</span>
        </p>
        {refusal.next ? <p className={styles.receiptNext}>{refusal.next}</p> : null}
      </div>
    );
  }

  return (
    <button
      className={dash.ghostlink}
      type="button"
      onClick={run}
      disabled={!indexable || write.status === "sending"}
      // The database's own flag, said in the one place a reader meets it: the
      // rail's foot already prints `indexing refused` for the deployment.
      title={indexable ? undefined : "This instance's database refuses writes."}
    >
      {write.status === "sending"
        ? "queueing…"
        : `Retry ${job.n_failed + job.degraded} failed or degraded item(s)`}
    </button>
  );
}

/** What the retry made, typed: the jobs, what they were selected from, what
 *  the new work inherited, and any batch the tool refused. */
function Receipt({ outcome }: { outcome: RetryOutcome }) {
  return (
    <div className={styles.receipt} role="status">
      <p className={styles.receiptLine}>
        <span>
          {outcome.selected} item(s) selected
          <Sep /> {outcome.jobs.length} job(s) queued
        </span>
        {outcome.jobs.map((queued) => (
          <DashLink key={queued.job_id} href={`${ROOT}/jobs/${encodeURIComponent(queued.job_id)}`}>
            <code>{queued.job_id}</code> ({queued.items})
          </DashLink>
        ))}
      </p>
      <p className={styles.receiptNext}>
        preserved: channels <code>{outcome.preserved.channels}</code>
        <Sep /> priority <code>{outcome.preserved.priority}</code>
        {outcome.preserved.tags.length ? (
          <>
            <Sep /> tags <code>{outcome.preserved.tags.join(",")}</code>
          </>
        ) : null}
      </p>
      {outcome.errors.map((error, index) => (
        <p className={`${styles.receiptLine} ${styles.outcomeBad}`} key={index}>
          <code>{error.error}</code>
          <span>{error.message}</span>
        </p>
      ))}
    </div>
  );
}
