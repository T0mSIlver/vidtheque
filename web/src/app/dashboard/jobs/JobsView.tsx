"use client";

import { useSearchParams } from "next/navigation";
import { dashboard, ROOT } from "@/lib/dashboard/client";
import { pick } from "@/lib/dashboard/query";
import { isRateLimited, useResource } from "@/lib/dashboard/resource";
import type { JobCard, Jobs } from "@/lib/dashboard/schemas";
import { at, DASH } from "@/lib/format";
import { notice, Notice, ReadFailure } from "../kit/notice";
import { Notes, Pager, table, TableCount } from "../kit/table";
import { Body, DashLink, Facts, PageHead, Sep, ui } from "../kit/ui";
import { refusalOf, useWriteSide } from "../kit/write";
import { usePatchedRows } from "../polling";
import { useSession } from "../session";
import { CancelControl } from "./CancelControl";
import { DEFAULTS, factsOf, FILTERS, Filters } from "./Filters";
import styles from "./jobs.module.css";
import { countsOf, jobHeadline, JobStates, livePoll, Progress, WallClock } from "./parts";

// The jobs table and its tick (dashboard.md §5.4, §16.3). The filters are the
// URL; every bound and predicate is `list_jobs`'s, and the page shows what the
// listing ran with rather than what was typed.

const PAGE_KEYS = [...FILTERS, "offset"];

export function JobsView() {
  const params = useSearchParams();
  const query = pick(params, PAGE_KEYS);
  const key = `jobs?${query}`;
  const jobs = useResource(key, (signal) => dashboard.jobs(query, signal), { pollMs: livePoll });
  const data = jobs.data;
  // The second hands stop with the tick; a 429 is a pause, not a stop.
  const moving = Boolean(data?.live) && (jobs.error === undefined || isRateLimited(jobs.error));

  return (
    <>
      <PageHead title="Jobs">
        <Facts
          facts={[
            ...factsOf(data?.filters),
            ["refresh", data ? `${Math.round(data.poll_ms / 1000)}s` : DASH],
          ]}
        />
      </PageHead>

      <Filters params={params} data={data} />

      {!data && jobs.error !== undefined ? (
        <ReadFailure error={jobs.error} onRetry={jobs.reload} />
      ) : (
        <Body ready={data !== undefined} height="40dvh">
          {/* A new baseline for a new listing, and for the first fresh reading
              after a cached one. */}
          {data ? (
            <Table
              key={`${key}:${jobs.isStale}`}
              data={data}
              stopped={jobs.error}
              polling={moving}
            />
          ) : null}
        </Body>
      )}
    </>
  );
}

function Table({ data, stopped, polling }: { data: Jobs; stopped: unknown; polling: boolean }) {
  const { rendered } = useWriteSide();
  const { rows, arrived } = usePatchedRows(data.jobs, (job) => job.job_id);
  // The listing's own flag; the session only for an instance predating it.
  const readonly = Boolean(useSession()?.readonly);
  const redacted = data.redacted ?? readonly;

  // A job queued since cannot be patched in, so the page says so — on an
  // empty listing too, which is the one most likely to grow a job.
  const arrival = arrived ? (
    <span className={styles.staleNote}>a job was queued since this page loaded, so reload</span>
  ) : null;

  if (!rows.length) {
    return (
      <>
        <Notes notes={data.notes} />
        {arrival ? (
          <p className={table.tablecount} role="status">
            <span>{arrival}</span>
          </p>
        ) : null}
        <Empty filters={data.filters} />
      </>
    );
  }

  return (
    <>
      <Notes notes={data.notes} />
      <TableCount shown={rows.length} hasMore={data.pagination.has_more}>
        {arrival}
        {stopped ? (
          <span className={styles.staleNote}>
            the live view stopped: {refusalOf(stopped).message}
          </span>
        ) : !data.live ? (
          <span className={styles.staleNote}>nothing is running, so this is a snapshot</span>
        ) : null}
      </TableCount>

      <div className={table.tablewrap}>
        <table className={`${table.grid} ${styles.jobs}`}>
          <caption className={ui.srOnly}>
            Jobs in the selected triage order, with what each one is waiting on
          </caption>
          <thead>
            <tr>
              <th scope="col">job</th>
              <th scope="col">state</th>
              <th scope="col">progress</th>
              <th scope="col">items</th>
              <th scope="col">created</th>
              <th scope="col">finished</th>
              <th scope="col" className={table.num}>
                wall clock
              </th>
              {rendered ? (
                <th scope="col" className={styles.colActions}>
                  action
                </th>
              ) : null}
            </tr>
          </thead>
          <tbody>
            {rows.map((job) => (
              <Row
                key={job.job_id}
                job={job}
                tickMs={data.poll_ms}
                actions={rendered}
                polling={polling}
              />
            ))}
          </tbody>
        </table>
      </div>

      <Pager
        limit={data.pagination.limit}
        offset={data.pagination.offset}
        hasMore={data.pagination.has_more}
        href={(offset) => pageLink(data, offset)}
        previous="← Newer"
        next={`Older ${data.pagination.limit} →`}
      />

      {redacted ? (
        <p className={notice.panelNote}>
          Source URLs and error text are not published on this instance.
        </p>
      ) : null}
    </>
  );
}

function Row({
  job,
  tickMs,
  actions,
  polling,
}: {
  job: JobCard;
  tickMs: number;
  actions: boolean;
  polling: boolean;
}) {
  const headline = jobHeadline(job);
  return (
    <tr>
      {/* What the job contains, never its submitted URL (§2.4). */}
      <th scope="row" className={styles.colJob} data-label="Job">
        <DashLink className={ui.rowTitle} href={`${ROOT}/jobs/${encodeURIComponent(job.job_id)}`}>
          {headline.muted ? (
            <span className={ui.muted} data-tone="muted">
              {headline.text}
            </span>
          ) : (
            headline.text
          )}
        </DashLink>
        {job.contents?.more ? (
          <span className={styles.rowMore}>+{job.contents.more} more</span>
        ) : null}
        <span className={ui.rowMeta}>
          <code>{job.job_id}</code>
          <Sep /> {job.kind}
          <Sep /> priority {job.priority}
          {job.contents?.channel ? (
            <>
              <Sep /> {job.contents.channel}
            </>
          ) : null}
          {job.degraded ? (
            <>
              <Sep /> <span className={styles.degraded}>{job.degraded} degraded</span>
            </>
          ) : null}
        </span>
      </th>
      <td data-label="State">
        <span className={styles.colState}>
          <JobStates job={job} moving={polling} />
        </span>
      </td>
      <td data-label="Progress">
        <Progress job={job} tickMs={tickMs} />
      </td>
      <td data-label="Items">{countsOf(job)}</td>
      <td data-label="Created">
        <time className={ui.nowrap}>{at(job.created_at)}</time>
      </td>
      <td data-label="Finished">
        <time className={ui.nowrap}>{job.finished_at ? at(job.finished_at) : DASH}</time>
      </td>
      <td className={table.num} data-label="Wall clock">
        <WallClock seconds={job.wall_s} live={job.live && polling} />
      </td>
      {actions ? (
        <td className={styles.colActions} data-label="Action">
          {job.live ? <CancelControl job={job} /> : <span className={ui.muted}>{DASH}</span>}
        </td>
      ) : null}
    </tr>
  );
}

/** No rows: a state filter that matched nothing, or an instance that has
 *  never queued anything. Read off the echo, so a fallen-back filter is not
 *  blamed. */
function Empty({ filters }: { filters: Jobs["filters"] }) {
  const { rendered } = useWriteSide();
  const narrowed = filters.state !== DEFAULTS.state;
  return (
    <Notice
      id="nojobs"
      title="No jobs to show."
      detail={
        narrowed ? (
          <>
            The filter is on <code>{filters.state}</code>.
          </>
        ) : (
          <>
            Nothing has ever been queued on this instance. <code>index-video</code> creates one.
          </>
        )
      }
      next={
        <>
          {narrowed ? <DashLink href={`${ROOT}/jobs?state=all`}>Show every job</DashLink> : null}
          {narrowed && rendered ? <Sep /> : null}
          {rendered ? (
            <DashLink data-empty-add="" href={`${ROOT}/index`}>
              Add videos
            </DashLink>
          ) : null}
        </>
      }
    />
  );
}

/** A page of this listing: every predicate from the payload, so page four is
 *  page four of this table (`jobs.html`'s `page_link`). */
function pageLink(data: Jobs, offset: number): string {
  const query = new URLSearchParams({
    state: data.filters.state,
    kind: data.filters.kind,
    error_code: data.filters.error_code ?? "",
    degraded: data.filters.degraded ? "1" : "0",
    order: data.filters.order,
    limit: String(data.pagination.limit),
    offset: String(offset),
  });
  return `${ROOT}/jobs?${query}`;
}
