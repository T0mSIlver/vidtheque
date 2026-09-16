"use client";

import { Pill } from "@/components/ui/Pill";
import { dashboard, ROOT } from "@/lib/dashboard/client";
import type { JobCard, RetryOutcome } from "@/lib/dashboard/schemas";
import { DASH } from "@/lib/format";
import controls from "../kit/controls.module.css";
import { notice, RefusalNotice } from "../kit/notice";
import { table } from "../kit/table";
import { DashLink, Sep, ui } from "../kit/ui";
import { focusOnArrival, useWrite, useWriteSide } from "../kit/write";
import styles from "./jobs.module.css";

// Retry a finished job's failed and degraded items (dashboard.md §16.2, §21).
// Disabled rather than absent when the database refuses writes: it feeds
// `index_video`. The reader stays on the job, so the receipt carries everything
// `retry.html` did; a `409` is a receipt with every batch refused.

/** `job.html`'s predicate: finished, an indexing kind, something to repair. */
export function retryable(job: JobCard): boolean {
  return (
    !job.live && ["index", "reindex"].includes(job.kind) && Boolean(job.n_failed || job.degraded)
  );
}

export function RetryControl({
  job,
  onRetried,
}: {
  job: JobCard;
  onRetried?: (outcome: RetryOutcome) => void;
}) {
  const { indexable } = useWriteSide();
  const [write, run] = useWrite(() => dashboard.retryJob(job.job_id), onRetried);
  const sending = write.status === "sending";

  if (write.status === "done") return <Receipt outcome={write.outcome} />;
  if (write.status === "failed") return <RefusalNotice error={write.error} variant="receipt" />;

  return (
    <span data-write="">
      <button
        className={controls.ghostlink}
        type="button"
        onClick={() => run()}
        disabled={!indexable}
        aria-disabled={sending || undefined}
        title={indexable ? undefined : "This instance's database refuses writes."}
      >
        {write.status === "sending"
          ? "queueing…"
          : `Retry ${job.n_failed + job.degraded} failed or degraded item(s)`}
      </button>
    </span>
  );
}

/** What the retry made, where the reader is standing (`retry.html`). */
function Receipt({ outcome }: { outcome: RetryOutcome }) {
  const from = `${ROOT}/jobs/${encodeURIComponent(outcome.from_job_id)}`;
  return (
    <div className={notice.receipt} role="status" tabIndex={-1} ref={focusOnArrival}>
      <p className={table.crumbs}>
        <DashLink href={`${ROOT}/jobs`}>Jobs</DashLink> <span aria-hidden="true">/</span> retry from{" "}
        <DashLink href={from}>
          <code>{outcome.from_job_id}</code>
        </DashLink>
      </p>

      <h3 className={styles.receiptTitle}>Repair queued</h3>
      <p className={styles.receiptMeta}>
        <span className={ui.mono}>{outcome.selected}</span> failed or degraded item(s) selected
      </p>
      {/* What the new work inherited; an empty tag list is the dash, a fact. */}
      <p className={styles.receiptMeta}>
        channels <span className={ui.mono}>{outcome.preserved.channels}</span>
        <Sep /> tags{" "}
        <span className={ui.mono}>
          {outcome.preserved.tags.length ? outcome.preserved.tags.join(",") : DASH}
        </span>
        <Sep /> priority <span className={ui.mono}>{outcome.preserved.priority}</span>
      </p>

      <h4 className={ui.panelTitle}>What the retry did</h4>
      <p className={notice.receiptNext}>
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
              <span className={ui.rowMeta}>
                <span className={ui.mono}>{queued.items}</span> item(s) queued
              </span>
            </li>
          ))}
        </ul>
      ) : null}

      {outcome.errors.map((error, index) => (
        <div key={index}>
          <p className={notice.receiptLine}>
            <Pill state={error.error ?? "E_UNKNOWN"} tone="bad" /> <span>{error.message}</span>
          </p>
          {error.next ? <p className={notice.receiptNext}>{error.next}</p> : null}
        </div>
      ))}
    </div>
  );
}
