"use client";

import { useCallback } from "react";
import { Pill } from "@/components/Pill";
import { dashboard, DashboardError, ROOT } from "@/lib/dashboard/client";
import type { JobDetail, JobEvent, JobItem } from "@/lib/dashboard/schemas";
import { at, count, DASH, duration, hms, iso } from "@/lib/format";
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
  Refusal,
  Sep,
  Unbroken,
  useDocumentTitle,
  useWriteSide,
} from "../../parts";
import { useArrivals } from "../../polling";
import { useSession } from "../../session";
import { useJobsPoll } from "../../useJobsPoll";
import { CancelControl } from "../CancelControl";
import styles from "../jobs.module.css";
import { countsOf, JobStates, Progress, tallyOf, useTicking, WallClock } from "../parts";
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
      // The message names the id itself — `"{id}" is not a job on this
      // instance.`, the sentence the page route answered with and the one
      // `writes.cancel_job` refuses a stale row with (§19). Policy text, so it
      // is printed and never composed here; the crumb is the way back to the
      // table, not a second telling of what went wrong.
      return (
        <>
          <Crumbs jobId={jobId} />
          <Refusal code={refusal.code} message={refusal.message} next={refusal.next} />
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

  return (
    <Loaded
      data={state.data}
      polling={state.polling}
      stopped={state.error}
      wasLive={state.wasLive}
    />
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
  /** Did this view watch the job run? The final-record note is owed to the
   *  reader whose page went stale under them and to nobody else — `job.html`
   *  kept the sentence hidden in the markup and the ticker revealed it on the
   *  reading where the job stopped. */
  wasLive: boolean;
}) {
  const { job } = data;
  const { rendered } = useWriteSide();
  useDocumentTitle(`Job ${job.job_id}`);
  // The projection this reading ran under, off the reading itself: every
  // `error_message` below is `null` under it, and "not published on this
  // instance" is only honest when the payload says which of the two absences
  // it is. The session is the fallback for an instance predating the field.
  const readonly = Boolean(useSession()?.readonly);
  const redacted = data.redacted ?? readonly;

  return (
    <>
      <Crumbs jobId={job.job_id} />

      <PageHead
        title={
          <>
            Job <code>{job.job_id}</code>
          </>
        }
      >
        {/* The states go on the title's baseline like every other page's
            header, and the countdown goes with them: on a deferred job it is
            the highest-value string on the surface and it used to sit fourth
            in a sentence. */}
        {/* §5.4 asks for the countdown "with `error_code` beside it": on a
            deferred job the code is the half that explains the clock, so it
            comes before the request that has not landed yet. */}
        <span className={styles.headstates}>
          <JobStates job={job} codeFirst moving={polling} />
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
        ) : !data.live && wasLive ? (
          // The wording is this page's and not `job.html`'s "reload for the
          // final record": there the tick patched a handful of fields and the
          // rest of the document stayed at the reading it was rendered from, so
          // a reload was what the sentence was for. Here the poll replaces the
          // whole payload, and the page the reader is looking at *is* the final
          // record — telling them to reload would be sending them to re-fetch
          // what they already have.
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

      {job.defer_s ? <Deferred job={data.job} moving={polling} /> : null}
      {job.error_code && !job.defer_s ? <JobError job={data.job} redacted={redacted} /> : null}

      <Cost data={data} polling={polling} />
      <Items data={data} redacted={redacted} />
      <Stages data={data} />
      <Degraded data={data} />
      <Events events={data.events} redacted={redacted} />
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
 *  was about, and the one fact no other surface carries.
 *
 *  The number inside the sentence counts down with the pill on the title's own
 *  baseline, and the whole notice goes when the wait does. `job.html` made this
 *  section part of the countdown for exactly that reason: a claimed job still
 *  being told it is being held is the failure mode the notice exists to
 *  prevent, arriving from the other side. */
function Deferred({ job, moving }: { job: JobDetail["job"]; moving: boolean }) {
  const left = useTicking(job.defer_s, moving, -1);
  if (left === null || left <= 0) return null;
  return (
    <section className={dash.notice} aria-labelledby="deferred">
      <h2 className={dash.noticeTitle} id="deferred">
        Waiting, not stuck
      </h2>
      <p className={dash.noticeDetail}>
        The job is <code>queued</code> with a <code>not_before</code> in the future, so{" "}
        <code>claim_next</code> will not pick it up for another <strong>{duration(left)}</strong>.
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

function JobError({ job, redacted }: { job: JobDetail["job"]; redacted: boolean }) {
  return (
    <section className={dash.noticeBad} aria-labelledby="joberr">
      <h2 className={dash.noticeBadTitle} id="joberr">
        <code>{job.error_code}</code>
      </h2>
      {job.error_message ? (
        <p className={dash.noticeDetail}>
          <span className={styles.errText}>{job.error_message}</span>
        </p>
      ) : redacted ? (
        // The projection drops the text and keeps the code (§2.4). Saying so is
        // the designed absent state — and only on the deployment that is
        // actually withholding it: a failure that had no message of its own is
        // not a redaction, and a page that says it is has told the reader to go
        // looking somewhere there is nothing to find.
        <p className={dash.noticeDetail}>The message is not published on this instance.</p>
      ) : null}
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
function Cost({ data, polling }: { data: JobDetail; polling: boolean }) {
  const { job } = data;
  const end = job.finished_at ? "finished" : "now";
  return (
    <Panel id="clocks" title="What it cost">
      <dl className={dash.figures}>
        {/* The one figure on this page that is still being taken. It counts up
            on a live job and stands still on a finished one, which is the same
            clock the table's own column keeps — a measurement that keeps
            counting is a lie, and so is one that stops while the work does
            not. */}
        <Figure label="wall clock" notes={[`created → ${end}`]}>
          <WallClock seconds={job.wall_s} live={job.live && polling} />
        </Figure>
        <Figure label="on the runner" notes={[`first claim → ${end}`]}>
          {duration(job.ran_s)}
        </Figure>
        <Figure label="queued for" notes={["created → first claim"]}>
          {duration(job.waited_s)}
        </Figure>
        {/* The states this job's items are actually in, from the grouped query
            over all of them — `job.html`'s own note. It is not the card's five
            buckets: those name every bucket including the empty ones, which is
            what a *percentage* has to be explained by and not what a job with
            ten items in one state should read as. `none` is the answer for a
            job with no items at all, which is a fact rather than five zeroes.
            An instance whose payload predates `counts` falls back to the
            tally, because "none" over ten items would be worse than verbose. */}
        <Figure label="items" notes={[itemNote(data)]}>
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

function Items({ data, redacted }: { data: JobDetail; redacted: boolean }) {
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
              <ItemRows key={item.item_id} item={item} redacted={redacted} />
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

/** The per-state tally under the item count — `counts` when the payload sends
 *  it, and `none` when it sends an empty one. */
function itemNote(data: JobDetail): string {
  if (!data.counts) return tallyOf(data.job);
  const entries = Object.entries(data.counts);
  if (!entries.length) return "none";
  return entries.map(([state, n]) => `${n} ${state}`).join(" · ");
}

function ItemRows({ item, redacted }: { item: JobItem; redacted: boolean }) {
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
                    {hms(item.duration_s)}
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
            ) : redacted ? (
              // Only where the deployment is withholding it. A stage that
              // failed with no message of its own is not a redaction.
              <span className={styles.errText}>message not published on this instance</span>
            ) : null}
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
  const focus = data.focus ?? null;
  // `job.html` drew this panel on `focus` and nothing else. The endpoint sends
  // all seven stages whether or not there is an item to attribute them to, so
  // a job with no focus would otherwise get seven `absent` rows under a
  // heading that names nobody — a table about no video.
  if (!focus || !stages?.length) return null;
  const subject = focus.title || focus.video_id || null;
  return (
    <Panel id="focus" subject={subject} title="Stage by stage">
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
        <p className={dash.emptyNote}>Nothing has been logged for this job.</p>
      )}
      <p className={styles.panelNote}>
        Newest first, {events.length} shown.
        {redacted ? " Message text is not published on this instance." : null}
      </p>
    </Panel>
  );
}

function Event({ event, isNew }: { event: JobEvent; isNew?: boolean }) {
  return (
    <li className={`${styles.event} ${isNew ? styles.isNew : ""}`}>
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
