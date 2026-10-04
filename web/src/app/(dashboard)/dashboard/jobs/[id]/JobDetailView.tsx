"use client";

import { useState } from "react";
import { useTicking } from "@/components/ui/RetryIn";
import { dashboard, DashboardError } from "@/lib/dashboard/client";
import { isRateLimited, useResource } from "@/lib/dashboard/resource";
import type { JobDetail } from "@/lib/dashboard/schemas";
import { duration } from "@/lib/format";
import { Notice, notice, ReadFailure, Refusal } from "@/components/dashboard/kit/notice";
import { Crumbs } from "@/components/dashboard/kit/table";
import { Page, PageHead, Panel, Pending, Title } from "@/components/dashboard/kit/ui";
import { refusalOf, useWriteSide } from "@/components/dashboard/kit/write";
import { useSession } from "@/components/dashboard/session";
import { CancelControl } from "../CancelControl";
import styles from "../jobs.module.css";
import { countsOf, JobStates, KINDS, livePoll, Progress, WallClock } from "../parts";
import { retryable, RetryControl } from "../RetryControl";
import { Degraded, Events, Items, Stages } from "./panels";

// One job's war story (dashboard.md §5.4): the deferral above the panels, the
// countdown on the title's baseline, the event log as a panel. Panels whose
// fields the payload does not send yet are absent, not empty.

export function JobDetailView({ jobId }: { jobId: string }) {
  const job = useResource(`job:${jobId}`, (signal) => dashboard.job(jobId, signal), {
    pollMs: livePoll,
  });
  const moving = Boolean(job.data?.live) && (job.error === undefined || isRateLimited(job.error));

  if (job.data) {
    return <Loaded data={job.data} polling={moving} stopped={job.error} />;
  }

  const refusal = job.error;
  if (refusal instanceof DashboardError && refusal.status === 404) {
    // Not a failed read: there is no such job, and a retry would say so again.
    return (
      <>
        <Crumbs section="jobs" label="Jobs" id={jobId} />
        <Refusal
          code={refusal.code}
          message={refusal.message}
          next={refusal.next}
          title="Unknown job"
        />
      </>
    );
  }
  return (
    <>
      <Crumbs section="jobs" label="Jobs" id={jobId} />
      <PageHead
        title={
          <>
            Job <code>{jobId}</code>
          </>
        }
      />
      {refusal !== undefined ? <ReadFailure error={refusal} onRetry={job.reload} /> : <Pending />}
    </>
  );
}

function Loaded({
  data,
  stopped,
  polling,
}: {
  data: JobDetail;
  stopped: unknown;
  polling: boolean;
}) {
  const { job } = data;
  const { rendered } = useWriteSide();
  const [retriedFrom, setRetriedFrom] = useState<string | null>(null);
  const readonly = Boolean(useSession()?.readonly);
  const redacted = data.redacted ?? readonly;

  return (
    <Page>
      <Title>{retriedFrom ? `Retry from ${retriedFrom}` : `Job ${job.job_id}`}</Title>
      <Crumbs section="jobs" label="Jobs" id={job.job_id} />

      <PageHead
        title={
          <>
            Job <code>{job.job_id}</code>
          </>
        }
        note={
          <>
            {KINDS[job.kind] ?? job.kind}, {countsOf(job)}, priority {job.priority}
          </>
        }
      >
        <span className={styles.headstates}>
          <JobStates job={job} codeFirst moving={polling} />
        </span>
      </PageHead>

      {/* A bar only while it moves: a finished job's bar is always full. */}
      {job.live || stopped ? (
        <p className={styles.progressline}>
          {job.live ? <Progress job={job} tickMs={data.poll_ms} wide /> : null}
          {stopped ? (
            <span className={styles.staleNote}>
              The live view stopped: {refusalOf(stopped).message}
            </span>
          ) : null}
        </p>
      ) : null}

      {/* Neither control re-reads the page: the tick shows a cancel, and a retry
          makes a different job that its receipt links to. */}
      {rendered && (job.live || retryable(job)) ? (
        <div className={styles.controls}>
          {job.live ? <CancelControl job={job} label="Cancel this job" /> : null}
          {retryable(job) ? (
            <RetryControl job={job} onRetried={(outcome) => setRetriedFrom(outcome.from_job_id)} />
          ) : null}
        </div>
      ) : null}

      {job.defer_s ? <Deferred job={job} moving={polling} /> : null}
      {job.error_code && !job.defer_s ? <JobError job={job} redacted={redacted} /> : null}

      <Cost data={data} polling={polling} />
      <Items data={data} redacted={redacted} />
      <Stages data={data} />
      <Degraded data={data} />
      <Events events={data.events} redacted={redacted} />
    </Page>
  );
}

/** Waiting, not stuck: `not_before` read back, counting down with the pill,
 *  and gone when the wait is. */
function Deferred({ job, moving }: { job: JobDetail["job"]; moving: boolean }) {
  const left = useTicking(job.defer_s, moving, -1);
  if (left === null || left <= 0) return null;
  return (
    <Notice
      id="deferred"
      title={`Waiting to retry, ${duration(left)} left`}
      detail={
        job.error_code ? (
          <>
            The retry was set after <code>{job.error_code}</code>.
          </>
        ) : null
      }
    />
  );
}

function JobError({ job, redacted }: { job: JobDetail["job"]; redacted: boolean }) {
  return (
    <Notice
      id="joberr"
      tone="bad"
      title={<code>{job.error_code}</code>}
      detail={
        job.error_message ? (
          <span className={styles.errText}>{job.error_message}</span>
        ) : redacted ? (
          // Only where the deployment withholds it; a failure with no message
          // is not a redaction.
          "The message is not published on this instance."
        ) : null
      }
    />
  );
}

/** Three durations: `started_at` is the first claim, so created → finished is
 *  the wall clock and the difference is time spent waiting. */
function Cost({ data, polling }: { data: JobDetail; polling: boolean }) {
  const { job } = data;
  return (
    <Panel id="clocks" title="Time">
      <ul className={styles.timeLine}>
        <li>
          <span className={styles.figure}>
            <WallClock seconds={job.wall_s} live={job.live && polling} />
          </span>{" "}
          wall clock
        </li>
        <li>
          <span className={styles.figure}>{duration(job.ran_s)}</span> running
        </li>
        <li>
          <span className={styles.figure}>{duration(job.waited_s)}</span> queued
        </li>
      </ul>
      {data.error_counts && Object.keys(data.error_counts).length ? (
        <p className={notice.panelNote}>
          Item errors:{" "}
          {Object.entries(data.error_counts).map(([code, n], index) => (
            <span key={code}>
              {index ? ", " : null}
              <code>{code}</code> ×{n}
            </span>
          ))}
        </p>
      ) : null}
    </Panel>
  );
}
