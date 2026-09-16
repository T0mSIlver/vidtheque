"use client";

import { Pill } from "@/components/Pill";
import { dashboard } from "@/lib/dashboard/client";
import type { JobCard } from "@/lib/dashboard/schemas";
import { notice, RefusalNotice } from "../kit/notice";
import { focusOnArrival, useWrite } from "../kit/write";

// Cancel, on a live job (dashboard.md §16.1, §21). Inline, because whether the
// job settled now or is still stopping is what the next tick cannot say. A
// settled job's button does not come back; a running one's does, since the
// runner has not stopped.

export function CancelControl({ job, label = "Cancel" }: { job: JobCard; label?: string }) {
  const [write, run] = useWrite(() => dashboard.cancelJob(job.job_id));
  const sending = write.status === "sending";

  if (write.status === "done") {
    const settled = !write.outcome.cancel_requested || write.outcome.state !== "running";
    return (
      <span
        className={notice.outcome}
        data-write=""
        role="status"
        tabIndex={-1}
        ref={focusOnArrival}
      >
        <Pill state={write.outcome.state} />
        {settled ? null : (
          <>
            <span>cancel requested</span>
            <button className={notice.rowbutton} type="button" onClick={() => run()}>
              {label}
            </button>
          </>
        )}
      </span>
    );
  }

  if (write.status === "failed") return <RefusalNotice error={write.error} variant="inline" />;

  return (
    <span data-write="">
      <button
        className={notice.rowbutton}
        type="button"
        onClick={() => run()}
        aria-disabled={sending || undefined}
      >
        {sending ? "cancelling…" : label}
      </button>
    </span>
  );
}
