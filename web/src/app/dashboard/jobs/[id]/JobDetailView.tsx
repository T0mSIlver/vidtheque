"use client";

import { useCallback, useEffect } from "react";
import { Pill } from "@/components/Pill";
import { dashboard, DashboardError, ROOT } from "@/lib/dashboard/client";
import type { JobDetail, JobEvent, JobItem } from "@/lib/dashboard/schemas";
import { at, count, DASH, duration, iso } from "@/lib/format";
import dash from "../../dashboard.module.css";
import {
  DashLink,
  Fact,
  Figure,
  PageHead,
  Panel,
  ReadFailure,
  Reading,
  refusalOf,
  Sep,
  Unbroken,
  useWriteSide,
} from "../../parts";
import { useJobsPoll } from "../../useJobsPoll";
import { CancelControl } from "../CancelControl";
import styles from "../jobs.module.css";
import { countsOf, JobStates, Progress, tallyOf } from "../parts";
import { retryable, RetryControl } from "../RetryControl";

// One job's war story — `templates/job.html`, reading
// `GET /dashboard/api/jobs/{job_id}` in the browser (dashboard.md §5.4).
//
// The motivating incident is the whole of the layout: a bot-check misread as a
// permanent failure, then correctly reclassified as throttling with backoff,
// after which the honest state was "waiting, and coming back" — and nothing
// rendered that. So the deferral notice is above the panels, the countdown is
// on the title's own baseline, and the event log is a panel rather than a
// footnote: a non-rate-limit deferral is recorded there and nowhere else.
//
// **Three of the Jinja page's panels are not here yet**, and it is the payload
// rather than the page: `views._job_detail` assembles `degraded`, `focus` with
// its seven `video_stages` rows, `counts`, `error_counts` and `items_capped`,
// and `/dashboard/api/jobs/{job_id}` sends none of them. Each is optional in
// `schemas.ts` and each panel below renders the day the payload carries it —
// so this file needs no change when it does, and shows nothing it cannot read
// meanwhile.

/** `views.EVENT_PREVIEW` — how many of the newest events stand in the open,
 *  with the rest behind the expander. A rendering bound rather than a fetch
 *  bound: the endpoint's own `EVENT_CAP` decides how many arrive. */
const EVENT_PREVIEW = 8;

export function JobDetailView({ jobId }: { jobId: string }) {
  const read = useCallback((signal: AbortSignal) => dashboard.job(jobId, signal), [jobId]);
  const state = useJobsPoll(read);

  if (state.status === "loading") return <Reading />;

  if (state.status === "failed") {
    const refusal = state.error;
    // An id that is not a job on this instance is not a failure to read it:
    // the read succeeded and the answer is "there is no such job". It gets the
    // refusal's own words and a way back to the table, not a retry button that
    // would produce this answer again.
    if (refusal instanceof DashboardError && refusal.status === 404) {
      return (
        <>
          <Crumbs jobId={jobId} />
          <PageHead title="Unknown job" />
          <section className={dash.notice} aria-labelledby="unknown">
            <h2 className={dash.noticeTitle} id="unknown">
              {refusal.message}
            </h2>
            <p className={dash.noticeDetail}>
              <code>{refusal.code}</code>
            </p>
            {refusal.next ? <p className={dash.noticeNext}>{refusal.next}</p> : null}
            <p className={dash.noticeNext}>
              <DashLink href={`${ROOT}/jobs`}>Back to the jobs table</DashLink>
            </p>
          </section>
        </>
      );
    }
    return (
      <>
        <Crumbs jobId={jobId} />
        <PageHead title="Job" />
        <ReadFailure error={state.error} onRetry={state.reload} />
      </>
    );
  }

  return <Loaded data={state.data} stopped={state.error} />;
}

function Loaded({ data, stopped }: { data: JobDetail; stopped: unknown }) {
  const { job } = data;
  const { rendered } = useWriteSide();

  useEffect(() => {
    document.title = `Job ${job.job_id} — vidtheque`;
  }, [job.job_id]);

  return (
    <>
      <Crumbs jobId={job.job_id} />

      <PageHead title={`Job ${job.job_id}`}>
        {/* The states go on the title's baseline like every other page's
            header, and the countdown goes with them: on a deferred job it is
            the highest-value string on the surface and it used to sit fourth
            in a sentence. */}
        <span className={styles.headstates}>
          <JobStates job={job} />
        </span>
      </PageHead>

      <p className={dash.meta}>
        <Unbroken>
          <span>{job.kind}</span>
        </Unbroken>
        <Sep />{" "}
        <Unbroken>
          <Fact label="priority" value={String(job.priority)} />
        </Unbroken>
        <Sep />{" "}
        <Unbroken>
          <span className={dash.mono}>{countsOf(job)}</span>
        </Unbroken>
      </p>

      <p className={styles.progressline}>
        <Progress job={job} tickMs={data.poll_ms} wide />
        {stopped ? (
          <span className={styles.staleNote}>
            the live view stopped: {refusalOf(stopped).message}
          </span>
        ) : !data.live ? (
          <span className={styles.staleNote}>
            this job has finished, so this is the final record
          </span>
        ) : null}
      </p>

      {/* The two controls, and neither reloads the page after it answers: a
          live job's tick is already running and will show the new state on its
          next reading, and a retry queues a *different* job, which the receipt
          links to. A page that re-read here would blank the outcome it had
          just been given. */}
      {rendered ? (
        <div className={styles.controls}>
          {job.live ? <CancelControl job={job} label="Cancel this job" /> : null}
          {retryable(job) ? <RetryControl job={job} /> : null}
        </div>
      ) : null}

      {job.defer_s ? <Deferred job={data.job} /> : null}
      {job.error_code && !job.defer_s ? <JobError job={data.job} /> : null}

      <Cost data={data} />
      <Items data={data} />
      <Stages data={data} />
      <Degraded data={data} />
      <Events events={data.events} />
    </>
  );
}

function Crumbs({ jobId }: { jobId: string }) {
  return (
    <p className={styles.crumbs}>
      <DashLink href={`${ROOT}/jobs`}>Jobs</DashLink> <span aria-hidden="true">/</span>{" "}
      <code>{jobId}</code>
    </p>
  );
}

/** Waiting, not stuck. `not_before` read back at last — the line the incident
 *  was about, and the one fact no other surface carries. */
function Deferred({ job }: { job: JobDetail["job"] }) {
  return (
    <section className={dash.notice} aria-labelledby="deferred">
      <h2 className={dash.noticeTitle} id="deferred">
        Waiting, not stuck
      </h2>
      <p className={dash.noticeDetail}>
        The job is <code>queued</code> with a <code>not_before</code> in the future, so{" "}
        <code>claim_next</code> will not pick it up for another{" "}
        <strong>{duration(job.defer_s)}</strong>.
        {job.error_code ? (
          <>
            {" "}
            The backoff was set after <code>{job.error_code}</code>.
          </>
        ) : null}
      </p>
    </section>
  );
}

function JobError({ job }: { job: JobDetail["job"] }) {
  return (
    <section className={dash.notice} aria-labelledby="joberr">
      <h2 className={dash.noticeTitle} id="joberr">
        <code>{job.error_code}</code>
      </h2>
      {job.error_message ? (
        <p className={dash.noticeDetail}>
          <span className={styles.errText}>{job.error_message}</span>
        </p>
      ) : (
        // The projection drops the text and keeps the code (§2.4). Saying so is
        // the designed absent state; a blank would read as a value that failed
        // to arrive.
        <p className={dash.noticeDetail}>The message is not published on this instance.</p>
      )}
    </section>
  );
}

/**
 * The three durations, and what separates them.
 *
 * `started_at` is the **first** claim, not the most recent, so
 * created → finished is the honest wall clock and first-claim → finished is
 * time on the runner. A deferred job spends the difference waiting, which is
 * the whole reason for printing both — a 92-minute overnight job reporting
 * "started 40s ago" is what the fix was for.
 */
function Cost({ data }: { data: JobDetail }) {
  const { job } = data;
  const end = job.finished_at ? "finished" : "now";
  return (
    <Panel id="clocks" title="What it cost">
      <dl className={dash.figures}>
        <Figure label="wall clock" notes={[`created → ${end}`]}>
          {duration(job.wall_s)}
        </Figure>
        <Figure label="on the runner" notes={[`first claim → ${end}`]}>
          {duration(job.ran_s)}
        </Figure>
        <Figure label="queued for" notes={["created → first claim"]}>
          {duration(job.waited_s)}
        </Figure>
        {/* The five buckets, from the card's own counts rather than from the
            item rows: the rows are capped and these are not, so a job with
            more items than the cap would otherwise print a tally that
            disagrees with the number above it. */}
        <Figure label="items" notes={[tallyOf(job)]}>
          {count(job.n_items)}
        </Figure>
      </dl>
      {data.error_counts && Object.keys(data.error_counts).length ? (
        <p className={styles.panelNote}>
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

function Items({ data }: { data: JobDetail }) {
  if (!data.items.length) {
    return (
      <Panel id="items" title="Items">
        <p className={dash.emptyNote}>This job has no items.</p>
      </Panel>
    );
  }
  return (
    <Panel id="items" title="Items">
      <div className={dash.tablewrap}>
        <table className={`${dash.grid} ${styles.items}`}>
          <caption className={dash.srOnly}>
            Each video in this job, its state and its retry counter
          </caption>
          <thead>
            <tr>
              <th scope="col" className={dash.num}>
                #
              </th>
              <th scope="col">video</th>
              <th scope="col">state</th>
              <th scope="col">stage</th>
              <th scope="col" className={dash.num}>
                attempts
              </th>
              <th scope="col" className={dash.num}>
                took
              </th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((item) => (
              <ItemRows key={item.item_id} item={item} />
            ))}
          </tbody>
        </table>
      </div>
      {data.items_capped ? (
        <p className={styles.panelNote}>Only the first {data.items.length} items are listed.</p>
      ) : null}
    </Panel>
  );
}

function ItemRows({ item }: { item: JobItem }) {
  return (
    <>
      <tr className={item.state === "failed" ? styles.bad : undefined}>
        <td className={dash.num} data-label="#">
          {item.seq}
        </td>
        <th scope="row" className={styles.colJob} data-label="Video">
          {item.video_id ? (
            <>
              <DashLink
                className={dash.rowTitle}
                href={`${ROOT}/videos/${encodeURIComponent(item.video_id)}`}
              >
                {item.title || item.video_id}
              </DashLink>
              <span className={dash.rowMeta}>
                {item.channel ? (
                  <>
                    {item.channel}
                    <Sep />{" "}
                  </>
                ) : null}
                {item.duration_s ? (
                  <>
                    {duration(item.duration_s)}
                    <Sep />{" "}
                  </>
                ) : null}
                <code>{item.video_id}</code>
              </span>
            </>
          ) : item.source_url ? (
            <>
              <span className={dash.rowTitle}>{item.source_url}</span>
              <span className={dash.rowMeta}>never resolved to a video</span>
            </>
          ) : (
            <>
              <span className={`${dash.rowTitle} ${dash.muted}`}>a submitted URL</span>
              <span className={dash.rowMeta}>
                never resolved to a video, and not published here
              </span>
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
            <span className={dash.muted}>{DASH}</span>
          )}
        </td>
        <td className={dash.num} data-label="Attempts">
          {item.attempts}/{item.max_attempts}
        </td>
        <td className={dash.num} data-label="Took">
          {duration(item.took_s)}
        </td>
      </tr>
      {item.error_code ? (
        <tr className={styles.rowError}>
          <td colSpan={6}>
            <span className={styles.label}>{item.error_code}</span>{" "}
            {item.error_message ? (
              <span className={styles.errText}>{item.error_message}</span>
            ) : (
              <span className={styles.errText}>message not published on this instance</span>
            )}
            {item.state === "queued" && item.retries_left ? (
              <span className={dash.muted}>
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

/** The seven `video_stages` rows for the item the job is on.
 *
 *  Read for the **one** item in focus, never for every item: seven stage rows
 *  per item is precisely the fan-out §6.3 forbids, and every other item's
 *  stages are one click away on its own video page. The payload does not carry
 *  either half today, so this panel is absent rather than empty. */
function Stages({ data }: { data: JobDetail }) {
  const stages = data.stages;
  if (!stages?.length) return null;
  const focus = data.focus ?? null;
  const subject = focus?.title || focus?.video_id || null;
  return (
    <Panel id="focus" title={subject ? `Stage by stage — ${subject}` : "Stage by stage"}>
      <div className={dash.tablewrap}>
        <table className={dash.grid}>
          <caption className={dash.srOnly}>
            The seven pipeline stages for the item this job is on
          </caption>
          <thead>
            <tr>
              <th scope="col">stage</th>
              <th scope="col">state</th>
              <th scope="col">started</th>
              <th scope="col" className={dash.num}>
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
                    <span className={dash.muted}>{DASH}</span>
                  )}
                </td>
                <td className={dash.num}>{duration(stage.took_s)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {focus?.video_id ? (
        <p className={styles.panelNote}>
          <DashLink href={`${ROOT}/videos/${encodeURIComponent(focus.video_id)}`}>
            This item&rsquo;s video page
          </DashLink>
          . Every other item&rsquo;s stages are on its own.
        </p>
      ) : null}
    </Panel>
  );
}

/** `done` with a stage missing underneath — the failure mode this project has
 *  already shipped twice. The count is on the job card; the rows are not on
 *  this payload, so the list appears the day they are. */
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
                <code className={dash.muted}>item {entry.seq}</code>
              )}
              <Sep /> <code>{entry.stage}</code> failed
            </span>
            {entry.error ? <span className={styles.errText}>{entry.error}</span> : null}
          </li>
        ))}
      </ul>
      <p className={styles.panelNote}>
        Only <code>fetch</code> and <code>stt</code> are essential, so these count as{" "}
        <code>done</code>. Re-index to get the missing channel back.
      </p>
    </Panel>
  );
}

/**
 * The event log, as a digest.
 *
 * Newest first, so the bounded preview is the half that answers "what just
 * happened"; an overnight batch's other fifty rows are one expander away with
 * a real count. This is the only record a non-rate-limit deferral has, which
 * is why watching one arrive is the point of the page being live at all.
 */
function Events({ events }: { events: JobEvent[] }) {
  const preview = events.slice(0, EVENT_PREVIEW);
  const older = events.slice(EVENT_PREVIEW);
  return (
    <Panel id="events" title="Event log">
      {events.length ? (
        <>
          <ol className={styles.events}>
            {preview.map((event) => (
              <Event event={event} key={event.id} />
            ))}
          </ol>
          {older.length ? (
            <details className={styles.digest}>
              <summary>
                <span className={styles.digestCount}>{older.length}</span> older event(s)
              </summary>
              <ol className={styles.events}>
                {older.map((event) => (
                  <Event event={event} key={event.id} />
                ))}
              </ol>
            </details>
          ) : null}
        </>
      ) : (
        <p className={dash.emptyNote}>Nothing has been logged for this job.</p>
      )}
      <p className={styles.panelNote}>Newest first, {events.length} shown.</p>
    </Panel>
  );
}

function Event({ event }: { event: JobEvent }) {
  return (
    <li className={styles.event}>
      <span className={styles.eventAt}>
        <time dateTime={iso(event.at)}>{at(event.at)}</time>
      </span>
      <Pill state={event.level} />
      {event.stage ? <code>{event.stage}</code> : null}
      <span className={`${styles.eventText} ${event.message ? "" : dash.muted}`}>
        {event.message || "message not published on this instance"}
      </span>
    </li>
  );
}
