"use client";

import { useSearchParams } from "next/navigation";
import { dashboard, ROOT } from "@/lib/dashboard/client";
import { pick } from "@/lib/dashboard/query";
import { isRateLimited, useResource } from "@/lib/dashboard/resource";
import type { JobCard, Jobs } from "@/lib/dashboard/schemas";
import { at, count } from "@/lib/format";
import { notice, Notice, ReadFailure } from "@/components/dashboard/kit/notice";
import { Notes, Pager, table } from "@/components/dashboard/kit/table";
import { Body, DashLink, Page, PageHead, Sep, ui, Wide } from "@/components/dashboard/kit/ui";
import { refusalOf, useWriteSide } from "@/components/dashboard/kit/write";
import { usePatchedRows } from "@/components/dashboard/polling";
import { useSession } from "@/components/dashboard/session";
import { CancelControl } from "./CancelControl";
import { DEFAULTS, FILTERS, Filters } from "./Filters";
import styles from "./jobs.module.css";
import { countsOf, jobHeadline, JobStates, KINDS, livePoll, Progress, WallClock } from "./parts";

// The jobs table and its tick (dashboard.md §5.4, §16.3). The filters are the
// URL; every bound and predicate is `list_jobs`'s, and the page shows what the
// listing ran with rather than what was typed.

const PAGE_KEYS = [...FILTERS, "offset"];

// The state filter as tabs, each with its count under the listing's kind:
// `queued` and `running` are both `active`; `cancelled` has no filter (§4.5),
// so it is counted in `all` only.
const TABS = [
  { filter: "active", label: "Active", count: (n: Jobs["by_state"]) => n.queued + n.running },
  { filter: "failed", label: "Failed", count: (n: Jobs["by_state"]) => n.failed },
  { filter: "done", label: "Done", count: (n: Jobs["by_state"]) => n.done },
  { filter: "all", label: "All", count: null },
] as const;

export function JobsView() {
  const params = useSearchParams();
  const query = pick(params, PAGE_KEYS);
  const key = `jobs?${query}`;
  const jobs = useResource(key, (signal) => dashboard.jobs(query, signal), { pollMs: livePoll });
  const data = jobs.data;
  // The second hands stop with the tick; a 429 is a pause, not a stop.
  const moving = Boolean(data?.live) && (jobs.error === undefined || isRateLimited(jobs.error));

  return (
    <Page>
      <PageHead title="Jobs" />

      <Tabs params={params} data={data} />
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
    </Page>
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
    <span className={styles.staleNote}>
      A job was queued since this page loaded; reload to see it.
    </span>
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

  // The cancel column is there only while a row can be cancelled.
  const cancellable = rendered && rows.some((job) => job.live);
  const notes =
    arrival ??
    (stopped ? (
      <span className={styles.staleNote}>The live view stopped: {refusalOf(stopped).message}</span>
    ) : null);

  return (
    <>
      <Notes notes={data.notes} />
      {notes ? (
        <p className={table.tablecount} role="status">
          {notes}
        </p>
      ) : null}

      <Wide>
        <div className={table.tablewrap}>
          <table className={`${table.grid} ${styles.jobs}`}>
            <caption className={ui.srOnly}>
              Jobs in the selected order, with what each one is waiting on
            </caption>
            <thead>
              <tr>
                <th scope="col">job</th>
                <th scope="col">state</th>
                <th scope="col">items</th>
                <th scope="col" className={table.num}>
                  wall clock
                </th>
                <th scope="col">finished</th>
                {cancellable ? <th scope="col" className={styles.colActions} /> : null}
              </tr>
            </thead>
            <tbody>
              {rows.map((job) => (
                <Row
                  key={job.job_id}
                  job={job}
                  tickMs={data.poll_ms}
                  actions={cancellable}
                  polling={polling}
                  showPriority={data.filters.order === "priority"}
                />
              ))}
            </tbody>
          </table>
        </div>
      </Wide>

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
  showPriority,
}: {
  job: JobCard;
  tickMs: number;
  actions: boolean;
  polling: boolean;
  showPriority: boolean;
}) {
  const headline = jobHeadline(job);
  return (
    <tr>
      {/* What the job contains, never its submitted URL (§2.4). */}
      <th scope="row" className={styles.colJob}>
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
        <span className={styles.rowMeta}>
          {KINDS[job.kind] ?? job.kind}
          {job.contents?.channel ? (
            <>
              <Sep /> {job.contents.channel}
            </>
          ) : null}
          {showPriority ? (
            <>
              <Sep /> priority {job.priority}
            </>
          ) : null}
          {job.degraded ? (
            <>
              <Sep /> <span className={styles.degraded}>{job.degraded} degraded</span>
            </>
          ) : null}
          <Sep /> <code>{job.job_id}</code>
        </span>
      </th>
      <td className={styles.cellState} data-label="State">
        <span className={styles.colState}>
          <JobStates job={job} moving={polling} />
        </span>
      </td>
      <td className={styles.cellItems} data-label="Items">
        {/* A bar only while it moves: a finished job's bar is always full. */}
        {job.live ? (
          <>
            <Progress job={job} tickMs={tickMs} />
            {job.n_items > 1 ? <span className={styles.liveCount}>{countsOf(job)}</span> : null}
          </>
        ) : (
          countsOf(job)
        )}
      </td>
      <td className={`${table.num} ${styles.cellClock}`} data-label="Wall clock">
        <WallClock seconds={job.wall_s} live={job.live && polling} />
      </td>
      <td className={styles.cellWhen} data-label="Finished">
        {job.finished_at ? <time className={ui.nowrap}>{at(job.finished_at)}</time> : null}
      </td>
      {actions ? (
        <td className={styles.colActions} data-label="Action">
          {job.live ? <CancelControl job={job} /> : null}
        </td>
      ) : null}
    </tr>
  );
}

/** The state filter as tabs, each with its count; the other filters keep
 *  their values across a tab. */
function Tabs({ params, data }: { params: URLSearchParams; data?: Jobs }) {
  const filters = data?.filters;
  const current = filters?.state ?? params.get("state") ?? DEFAULTS.state;
  // What the listing ran with, the URL until it answers.
  const kept: Record<string, string | null> = {
    kind: filters?.kind ?? params.get("kind"),
    error_code: filters ? filters.error_code : params.get("error_code"),
    order: filters?.order ?? params.get("order"),
    degraded: filters ? (filters.degraded ? "1" : null) : params.get("degraded"),
    limit: params.get("limit"),
  };
  const href = (state: string) => {
    const next = new URLSearchParams();
    for (const key of FILTERS) {
      const value = key === "state" ? state : kept[key];
      if (value && value !== DEFAULTS[key]) next.set(key, value);
    }
    const query = next.toString();
    return query ? `${ROOT}/jobs?${query}` : `${ROOT}/jobs`;
  };
  return (
    <nav className={styles.tabs} aria-label="Job state">
      {TABS.map((tab) => {
        const n = data && tab.count ? tab.count(data.by_state) : null;
        return (
          <DashLink
            key={tab.filter}
            className={styles.tab}
            href={href(tab.filter)}
            aria-current={current === tab.filter ? "page" : undefined}
            scroll={false}
          >
            {tab.label}
            {n ? <span className={styles.tabCount}>{count(n)}</span> : null}
          </DashLink>
        );
      })}
    </nav>
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
