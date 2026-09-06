"use client";

import { useCallback } from "react";
import { dashboard, ROOT } from "@/lib/dashboard/client";
import type { JobCard, RetryOutcome } from "@/lib/dashboard/schemas";
import { DASH } from "@/lib/format";
import dash from "../dashboard.module.css";
import { DashLink, refusalOf, Sep, useDocumentTitle, useWrite, useWriteSide } from "../parts";
import styles from "./jobs.module.css";

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
// The receipt is read from the response rather than followed. `writes.retry_job`
// sends the browser to the new job when there is exactly one and nothing went
// wrong; here the reader stays on the job they were reading, so **the receipt
// has to carry everything `retry.html` carried** — where the repair came from,
// what was selected, what the new work inherited, what it did *not* requeue,
// and every batch the tool refused with the sentence saying what to do about
// it. A `409` is a receipt too: it means every batch was refused, and the
// refusals are on it.

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

/** What the retry made — `templates/retry.html`, in the place the reader is
 *  standing rather than on a page they were sent to.
 *
 *  The document is renamed for as long as this is on screen, because the Jinja
 *  receipt *was* a document and had a name: a tab that still says "Job
 *  job_finished01" over a repair that has just been queued is the one thing a
 *  reader might have three of. */
function Receipt({ outcome }: { outcome: RetryOutcome }) {
  const from = `${ROOT}/jobs/${encodeURIComponent(outcome.from_job_id)}`;
  useDocumentTitle(`Retry from ${outcome.from_job_id}`);
  return (
    <div className={styles.receipt} role="status">
      <p className={styles.crumbs}>
        <DashLink href={`${ROOT}/jobs`}>Jobs</DashLink> <span aria-hidden="true">/</span> retry from{" "}
        <DashLink href={from}>
          <code>{outcome.from_job_id}</code>
        </DashLink>
      </p>

      <h3 className={styles.receiptTitle}>Repair queued</h3>
      <p className={styles.receiptMeta}>
        <span className={dash.mono}>{outcome.selected}</span> failed or degraded item(s) selected
      </p>
      {/* What the new work inherited, which is the half of a retry nobody can
          see from the job it made. `tags` is the em dash when there are none:
          the fact is that nothing was carried over, and an omitted line reads
          as a fact nobody checked. */}
      <p className={styles.receiptMeta}>
        channels <span className={dash.mono}>{outcome.preserved.channels}</span>
        <Sep /> tags{" "}
        <span className={dash.mono}>
          {outcome.preserved.tags.length ? outcome.preserved.tags.join(",") : DASH}
        </span>
        <Sep /> priority <span className={dash.mono}>{outcome.preserved.priority}</span>
      </p>

      {/* What was *not* requeued, which is the sentence the receipt exists for:
          a repair that silently re-ran the successful half would cost an
          overnight batch twice. */}
      <p className={styles.receiptNext}>
        Only items that failed, or finished with a failed optional stage, were sent back through the
        index service. Successful items from <DashLink href={from}>{outcome.from_job_id}</DashLink>{" "}
        were left alone. A retry resumes each video at its outstanding stages; it does not force a
        rebuild of stages that already succeeded.
      </p>

      {outcome.jobs.length ? (
        <ul className={styles.joblist}>
          {outcome.jobs.map((queued) => (
            <li key={queued.job_id}>
              <DashLink href={`${ROOT}/jobs/${encodeURIComponent(queued.job_id)}`}>
                <code>{queued.job_id}</code>
              </DashLink>
              <span className={dash.rowMeta}>
                <span className={dash.mono}>{queued.items}</span> item(s) queued
              </span>
            </li>
          ))}
        </ul>
      ) : null}

      {outcome.errors.map((error, index) => (
        <div key={index}>
          <p className={`${styles.receiptLine} ${styles.outcomeBad}`}>
            <code>{error.error}</code>
            <span>{error.message}</span>
          </p>
          {/* The sentence saying what to do about the batch that was refused —
              policy text, Python's, and the one line a receipt with a refusal
              on it is for. */}
          {error.next ? <p className={styles.receiptNext}>{error.next}</p> : null}
        </div>
      ))}
    </div>
  );
}
