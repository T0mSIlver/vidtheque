"use client";

import { useState } from "react";
import { Pill } from "@/components/Pill";
import { useTicking } from "@/components/RetryIn";
import { dashboard, DashboardError, ROOT } from "@/lib/dashboard/client";
import { isRateLimited, useResource } from "@/lib/dashboard/resource";
import type { JobDetail, JobEvent, JobItem } from "@/lib/dashboard/schemas";
import { at, count, DASH, duration, hms, iso } from "@/lib/format";
import { Notice, notice, ReadFailure, Refusal } from "../../kit/notice";
import { Crumbs, table } from "../../kit/table";
import {
  DashLink,
  Fact,
  Figure,
  PageHead,
  Panel,
  Pending,
  Sep,
  Title,
  ui,
  Unbroken,
} from "../../kit/ui";
import { refusalOf, useWriteSide } from "../../kit/write";
import { useArrivals } from "../../polling";
import { useSession } from "../../session";
import { CancelControl } from "../CancelControl";
import styles from "../jobs.module.css";
import { countsOf, JobStates, livePoll, Progress, tallyOf, WallClock } from "../parts";
import { retryable, RetryControl } from "../RetryControl";

// One job's war story (dashboard.md §5.4): the deferral above the panels, the
// countdown on the title's baseline, the event log as a panel. Panels whose
// fields the payload does not send yet are absent, not empty.

/** `views.EVENT_PREVIEW`: a rendering bound, not a fetch bound. */
const EVENT_PREVIEW = 8;

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

function Items({ data, redacted }: { data: JobDetail; redacted: boolean }) {
  return (
    <Panel id="items" title="Items">
      {data.items.length ? (
        <div className={table.tablewrap}>
          <table className={`${table.grid} ${styles.items}`}>
            <caption className={ui.srOnly}>
              Each video in this job, its state and its retry counter
            </caption>
            <thead>
              <tr>
                <th scope="col" className={table.num}>
                  #
                </th>
                <th scope="col">video</th>
                <th scope="col">state</th>
                <th scope="col">stage</th>
                <th scope="col" className={table.num}>
                  attempts
                </th>
                <th scope="col" className={table.num}>
                  took
                </th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((item) => (
                <ItemRows key={item.item_id} item={item} redacted={redacted} />
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className={ui.emptyNote}>This job has no items.</p>
      )}
      {data.items_capped ? (
        <p className={notice.panelNote}>Only the first {data.items.length} items are listed.</p>
      ) : null}
    </Panel>
  );
}

function ItemRows({ item, redacted }: { item: JobItem; redacted: boolean }) {
  return (
    <>
      <tr className={item.state === "failed" ? styles.bad : undefined}>
        <td className={table.num} data-label="#">
          {item.seq}
        </td>
        <th scope="row" className={styles.colJob} data-label="Video">
          {item.video_id ? (
            <>
              <DashLink
                className={ui.rowTitle}
                href={`${ROOT}/videos/${encodeURIComponent(item.video_id)}`}
              >
                {item.title || item.video_id}
              </DashLink>
              <span className={ui.rowMeta}>
                {item.channel ? (
                  <>
                    {item.channel}
                    <Sep />{" "}
                  </>
                ) : null}
                {item.duration_s ? (
                  <>
                    {hms(item.duration_s)}
                    <Sep />{" "}
                  </>
                ) : null}
                <code>{item.video_id}</code>
              </span>
            </>
          ) : item.source_url ? (
            <>
              <span className={ui.rowTitle}>{item.source_url}</span>
              <span className={ui.rowMeta}>never resolved to a video</span>
            </>
          ) : (
            <>
              <span className={`${ui.rowTitle} ${ui.muted}`}>a submitted URL</span>
              <span className={ui.rowMeta}>never resolved to a video, and not published here</span>
            </>
          )}
        </th>
        <td data-label="State">
          <Pill state={item.state} />
        </td>
        <td data-label="Stage">
          {item.stage ? (
            <code>
              {item.stage} {item.stage_pct}%
            </code>
          ) : (
            <span className={ui.muted}>{DASH}</span>
          )}
        </td>
        <td className={table.num} data-label="Attempts">
          {item.attempts}/{item.max_attempts}
        </td>
        <td className={table.num} data-label="Took">
          {duration(item.took_s)}
        </td>
      </tr>
      {item.error_code ? (
        <tr className={styles.rowError}>
          <td colSpan={6}>
            <span className={styles.label}>{item.error_code}</span>{" "}
            {item.error_message ? (
              <span className={styles.errText}>{item.error_message}</span>
            ) : redacted ? (
              <span className={styles.errText}>message not published on this instance</span>
            ) : null}
            {item.state === "queued" && item.retries_left ? (
              <span className={ui.muted}>
                {" "}
                · {item.retries_left} attempt(s) left, so it is queued to try again
              </span>
            ) : null}
          </td>
        </tr>
      ) : null}
    </>
  );
}

/** The seven stages of the one item in focus, never every item's (§6.3). */
function Stages({ data }: { data: JobDetail }) {
  const stages = data.stages;
  const focus = data.focus ?? null;
  if (!focus || !stages?.length) return null;
  return (
    <Panel id="focus" subject={focus.title || focus.video_id || null} title="Stage by stage">
      <div className={table.tablewrap}>
        <table className={table.grid}>
          <caption className={ui.srOnly}>
            The seven pipeline stages for the item this job is on
          </caption>
          <thead>
            <tr>
              <th scope="col">stage</th>
              <th scope="col">state</th>
              <th scope="col">started</th>
              <th scope="col" className={table.num}>
                took
              </th>
            </tr>
          </thead>
          <tbody>
            {stages.map((stage) => (
              <tr key={stage.stage} className={stage.state === "failed" ? styles.bad : undefined}>
                <th scope="row">
                  <code>{stage.stage}</code>
                </th>
                <td>
                  <Pill state={stage.state} />
                </td>
                <td>
                  {stage.started_at ? (
                    <time dateTime={iso(stage.started_at)}>{at(stage.started_at)}</time>
                  ) : (
                    <span className={ui.muted}>{DASH}</span>
                  )}
                </td>
                <td className={table.num}>{duration(stage.took_s)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {focus.video_id ? (
        <p className={notice.panelNote}>
          <DashLink href={`${ROOT}/videos/${encodeURIComponent(focus.video_id)}`}>
            This item&rsquo;s video page
          </DashLink>
          . Every other item&rsquo;s stages are on its own.
        </p>
      ) : null}
    </Panel>
  );
}

/** `done` with a stage missing underneath. */
function Degraded({ data }: { data: JobDetail }) {
  const rows = data.degraded;
  if (!rows?.length) return null;
  return (
    <Panel id="degraded" title="Finished with something missing">
      <ul className={styles.rowlist}>
        {rows.map((entry) => (
          <li className={styles.minirow} key={`${entry.seq}-${entry.stage}`}>
            <span>
              {entry.video_id ? (
                <DashLink href={`${ROOT}/videos/${encodeURIComponent(entry.video_id)}`}>
                  <code>{entry.video_id}</code>
                </DashLink>
              ) : (
                <code className={ui.muted}>item {entry.seq}</code>
              )}
              <Sep /> <code>{entry.stage}</code> failed
            </span>
            {entry.error ? <span className={styles.errText}>{entry.error}</span> : null}
          </li>
        ))}
      </ul>
      <p className={notice.panelNote}>
        Only <code>fetch</code> and <code>stt</code> are essential, so these count as{" "}
        <code>done</code>. Re-index to get the missing channel back.
      </p>
    </Panel>
  );
}

/** Newest first, a bounded preview and the rest behind an expander. An entry
 *  that landed while the page was open is marked. */
function Events({ events, redacted }: { events: JobEvent[]; redacted: boolean }) {
  const arrived = useArrivals(events, (event) => event.id);
  const preview = events.slice(0, EVENT_PREVIEW);
  const older = events.slice(EVENT_PREVIEW);
  return (
    <Panel id="events" title="Event log">
      {events.length ? (
        <>
          <ol className={styles.events}>
            {preview.map((event) => (
              <Event event={event} key={event.id} isNew={arrived.has(event.id)} />
            ))}
          </ol>
          {older.length ? (
            <details className={styles.digest}>
              <summary>
                <span className={styles.digestCount}>{older.length}</span> older event(s)
              </summary>
              <ol className={styles.events}>
                {older.map((event) => (
                  <Event event={event} key={event.id} isNew={arrived.has(event.id)} />
                ))}
              </ol>
            </details>
          ) : null}
        </>
      ) : (
        <p className={ui.emptyNote}>Nothing has been logged for this job.</p>
      )}
      <p className={notice.panelNote}>
        Newest first, {events.length} shown.
        {redacted ? " Message text is not published on this instance." : null}
      </p>
    </Panel>
  );
}

function Event({ event, isNew }: { event: JobEvent; isNew?: boolean }) {
  return (
    <li className={`${styles.event} ${isNew ? styles.isNew : ""}`} data-new={isNew || undefined}>
      <span className={styles.eventAt}>
        <time dateTime={iso(event.at)}>{at(event.at)}</time>
      </span>
      <Pill state={event.level} />
      {event.stage ? <code>{event.stage}</code> : null}
      <span className={`${styles.eventText} ${event.message ? "" : ui.muted}`}>
        {event.message || "message not published on this instance"}
      </span>
    </li>
  );
}
