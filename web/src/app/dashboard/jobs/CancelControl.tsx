"use client";

import { useCallback } from "react";
import { Pill } from "@/components/Pill";
import { dashboard } from "@/lib/dashboard/client";
import type { JobCard } from "@/lib/dashboard/schemas";
import dash from "../dashboard.module.css";
import { refusalOf, useWrite } from "../parts";
import styles from "./jobs.module.css";

// Cancel, on a job that can still be cancelled — `POST /dashboard/jobs/{id}/cancel`
// (dashboard.md §16.1, §21).
//
// Rendered on `job.live` and nothing else, which is `jobs.html`'s and
// `job.html`'s own condition: a terminal job refuses the action, and a control
// that exists to be refused is worse UI than no control. Whether it is drawn at
// all is the deployment's answer, not this component's — `useWriteSide` in
// `parts.tsx` reads that, and the two pages ask before they render this.
//
// It is a `fetch` rather than the Jinja page's real `<form method="post">`
// because it needs the answer *inline*: cancelling queued work settles it now,
// cancelling running work does not, and which of the two just happened is
// precisely what the next 2 s tick cannot say. The browser sends the session
// cookie and its own `Sec-Fetch-Site: same-origin` with it, which is the
// positive same-origin evidence §3.3 asks of an ambient credential — there is
// no CSRF token on this surface and none is planned.
//
// What replaces the button is what the route answered. On a job the write
// *settled* the button does not come back: a second POST against a settled job
// is refused `E_BAD_PARAM`, and there is nothing left to cancel. On a running
// job it does, beside the outcome, because that job is still live and the
// runner has not stopped — `jobs.html` and `job.html` drew Cancel on
// `job.live` and the POST landed back on a page where the job still was. A
// reader who cannot re-cancel a running job without reloading has lost the one
// control the page exists to offer them.

export function CancelControl({ job, label = "Cancel" }: { job: JobCard; label?: string }) {
  const send = useCallback(() => dashboard.cancelJob(job.job_id), [job.job_id]);
  const [write, run] = useWrite(send);

  if (write.status === "done") {
    const settled = !write.outcome.cancel_requested || write.outcome.state !== "running";
    return (
      <span className={styles.outcome} role="status">
        <Pill state={write.outcome.state} />
        {/* The store's own word for where the job is now, and — when the
            runner has not stopped yet — the fact that it has been asked to.
            Both are values off the payload; neither is a sentence this page
            composed about what will happen next. */}
        {settled ? null : (
          <>
            <span>cancel requested</span>
            <button className={styles.rowbutton} type="button" onClick={run}>
              {label}
            </button>
          </>
        )}
      </span>
    );
  }

  if (write.status === "failed") {
    const refusal = refusalOf(write.error);
    return (
      <span className={`${styles.outcome} ${styles.outcomeBad}`} role="status">
        <code>{refusal.code}</code> <span>{refusal.message}</span>
        {/* The refusal's third sentence, which says what to do instead —
            "only queued or running jobs can be cancelled". The Jinja refusal
            was a whole page and printed it under a heading; inline it is the
            line after the message, and it is Python's either way. */}
        {refusal.next ? <span className={dash.outcomeNext}>{refusal.next}</span> : null}
      </span>
    );
  }

  return (
    <button
      className={styles.rowbutton}
      type="button"
      onClick={run}
      disabled={write.status === "sending"}
    >
      {write.status === "sending" ? "cancelling…" : label}
    </button>
  );
}
