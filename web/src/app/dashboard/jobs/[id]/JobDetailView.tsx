"use client";

import { useState } from "react";
import { useTicking } from "@/components/ui/RetryIn";
import { dashboard, DashboardError } from "@/lib/dashboard/client";
import { isRateLimited, useResource } from "@/lib/dashboard/resource";
import type { JobDetail } from "@/lib/dashboard/schemas";
import { count, duration } from "@/lib/format";
import { Notice, notice, ReadFailure, Refusal } from "../../kit/notice";
import { Crumbs } from "../../kit/table";
import { Fact, Figure, PageHead, Panel, Pending, Sep, Title, ui, Unbroken } from "../../kit/ui";
import { refusalOf, useWriteSide } from "../../kit/write";
import { useSession } from "../../session";
import { CancelControl } from "../CancelControl";
import styles from "../jobs.module.css";
import { countsOf, JobStates, livePoll, Progress, tallyOf, WallClock } from "../parts";
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
  const [watched, setWatched] = useState<string | null>(null);
  // Did this view watch the job run? The final-record note is owed only then.
  if (job.data?.live && !job.isStale && watched !== jobId) setWatched(jobId);

  if (job.data) {
    return (
      <Loaded data={job.data} polling={moving} stopped={job.error} wasLive={watched === jobId} />
    );
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
  wasLive,
}: {
  data: JobDetail;
  stopped: unknown;
  polling: boolean;
  wasLive: boolean;
}) {
  const { job } = data;
  const { rendered } = useWriteSide();
  const [retriedFrom, setRetriedFrom] = useState<string | null>(null);
  const readonly = Boolean(useSession()?.readonly);
  const redacted = data.redacted ?? readonly;

  return (
    <>
      <Title>{retriedFrom ? `Retry from ${retriedFrom}` : `Job ${job.job_id}`}</Title>
      <Crumbs section="jobs" label="Jobs" id={job.job_id} />

      <PageHead
        title={
          <>
            Job <code>{job.job_id}</code>
          </>
        }
      >
        <span className={styles.headstates}>
          <JobStates job={job} codeFirst moving={polling} />
        </span>
      </PageHead>

      <p className={ui.meta}>
        <Unbroken>
          <span>{job.kind}</span>
        </Unbroken>
        <Sep />{" "}
        <Unbroken>
          <Fact label="priority" value={String(job.priority)} />
        </Unbroken>
        <Sep />{" "}
        <Unbroken>
          <span className={ui.mono}>{countsOf(job)}</span>
        </Unbroken>
      </p>

      <p className={styles.progressline}>
        <Progress job={job} tickMs={data.poll_ms} wide />
        {stopped ? (
          <span className={styles.staleNote}>
            the live view stopped: {refusalOf(stopped).message}
          </span>
        ) : !data.live && wasLive ? (
          // The poll replaces the whole payload, so this page is the record.
          <span className={styles.staleNote}>
            this job has finished, so this is the final record
          </span>
        ) : null}
      </p>

      {/* Neither control re-reads the page: the tick shows a cancel, and a retry
          makes a different job that its receipt links to. */}
      {rendered ? (
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
    </>
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
      title="Waiting, not stuck"
      detail={
        <>
          The job is <code>queued</code> with a <code>not_before</code> in the future, so{" "}
          <code>claim_next</code> will not pick it up for another <strong>{duration(left)}</strong>.
          {job.error_code ? (
            <>
              {" "}
              The backoff was set after <code>{job.error_code}</code>.
            </>
          ) : null}
        </>
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
  const end = job.finished_at ? "finished" : "now";
  return (
    <Panel id="clocks" title="What it cost">
      <dl className={ui.figures}>
        <Figure label="wall clock" notes={[`created → ${end}`]}>
          <WallClock seconds={job.wall_s} live={job.live && polling} />
        </Figure>
        <Figure label="on the runner" notes={[`first claim → ${end}`]}>
          {duration(job.ran_s)}
        </Figure>
        <Figure label="queued for" notes={["created → first claim"]}>
          {duration(job.waited_s)}
        </Figure>
        <Figure label="items" notes={[itemNote(data)]}>
          {count(job.n_items)}
        </Figure>
      </dl>
      {data.error_counts && Object.keys(data.error_counts).length ? (
        <p className={notice.panelNote}>
          <span className={styles.label}>item error codes</span>{" "}
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

/** The states the items are in (`counts`), `none` for no items, or the card's
 *  tally on an instance that predates `counts`. */
function itemNote(data: JobDetail): string {
  if (!data.counts) return tallyOf(data.job);
  const entries = Object.entries(data.counts);
  if (!entries.length) return "none";
  return entries.map(([state, n]) => `${n} ${state}`).join(" · ");
}
