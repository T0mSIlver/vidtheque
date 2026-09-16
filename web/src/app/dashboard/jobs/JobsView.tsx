"use client";

import { useSearchParams } from "next/navigation";
import { dashboard, ROOT } from "@/lib/dashboard/client";
import { pick, withQuery } from "@/lib/dashboard/query";
import { isRateLimited, useResource } from "@/lib/dashboard/resource";
import type { JobCard, Jobs } from "@/lib/dashboard/schemas";
import { at, DASH } from "@/lib/format";
import controls from "../kit/controls.module.css";
import { FilterBand } from "../kit/FilterBand";
import { notice, Notice, ReadFailure } from "../kit/notice";
import { Notes, Pager, table, TableCount } from "../kit/table";
import { DashLink, Fact, Facts, Body, PageHead, Sep, ui, Unbroken } from "../kit/ui";
import { refusalOf, useWriteSide } from "../kit/write";
import { usePatchedRows } from "../polling";
import { useSession } from "../session";
import { CancelControl } from "./CancelControl";
import styles from "./jobs.module.css";
import { countsOf, jobHeadline, JobStates, livePoll, Progress, WallClock } from "./parts";

// The jobs table and its tick (dashboard.md §5.4, §16.3). The filters are the
// URL; every bound and predicate is `list_jobs`'s, and the page shows what the
// listing ran with rather than what was typed.

const FILTERS = ["state", "kind", "error_code", "order", "degraded", "limit"] as const;
const PAGE_KEYS = [...FILTERS, "offset"];

// The pickers' words (`views._JOB_STATES` and friends): options, not bounds.
const STATES = ["all", "active", "failed", "done"];
const KINDS = ["all", "index", "reindex", "delete", "follow_check"];
const ORDERS = ["newest", "priority", "wall_clock"];

/** Values the API would use anyway, left off a link. */
const DEFAULTS: Record<string, string> = { state: "all", kind: "all", order: "newest" };

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
        <Facts facts={factsOf(data?.filters)} />{" "}
        <Unbroken>
          <Fact label="refresh" value={data ? `${Math.round(data.poll_ms / 1000)}s` : DASH} />
        </Unbroken>
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

/** `state` and `order` always (the table is read in an order); the rest only
 *  when they narrow. Dashes until the listing answers. */
function factsOf(filters?: Jobs["filters"]): [string, string][] {
  if (!filters) {
    return [
      ["state", DASH],
      ["order", DASH],
    ];
  }
  const facts: [string, string][] = [["state", filters.state]];
  if (filters.kind !== DEFAULTS.kind) facts.push(["kind", filters.kind]);
  if (filters.error_code) facts.push(["error code", filters.error_code]);
  if (filters.degraded) facts.push(["degraded", "only"]);
  facts.push(["order", filters.order]);
  return facts;
}

/** The band, seeded from what the listing ran with (the URL until it answers). */
function Filters({ params, data }: { params: URLSearchParams; data?: Jobs }) {
  const asked = (key: string, fallback = "") => params.get(key) ?? fallback;
  const filters = data?.filters;
  const values = {
    state: filters?.state ?? asked("state", "all"),
    kind: filters?.kind ?? asked("kind", "all"),
    order: filters?.order ?? asked("order", "newest"),
    error_code: filters ? (filters.error_code ?? "") : asked("error_code"),
    degraded: filters ? filters.degraded : asked("degraded") === "1",
    limit: data ? String(data.pagination.limit) : asked("limit"),
  };

  function toUrl(form: FormData) {
    const next = new URLSearchParams();
    for (const key of FILTERS) {
      const entry = form.get(key);
      if (typeof entry !== "string") continue;
      const chosen = entry.trim();
      if (chosen && chosen !== DEFAULTS[key]) next.set(key, chosen);
    }
    return withQuery(`${ROOT}/jobs`, next);
  }

  return (
    <FilterBand values={values} toUrl={toUrl}>
      <div className={`${controls.field} ${controls.pickField}`}>
        <label htmlFor="f-jobstate">State</label>
        <span className={controls.pick}>
          <select id="f-jobstate" name="state" defaultValue={values.state}>
            {STATES.map((entry) => (
              <option key={entry} value={entry}>
                {entry}
              </option>
            ))}
          </select>
        </span>
      </div>
      <div className={`${controls.field} ${controls.pickField}`}>
        <label htmlFor="f-jobkind">Kind</label>
        <span className={controls.pick}>
          <select id="f-jobkind" name="kind" defaultValue={values.kind}>
            {KINDS.map((entry) => (
              <option key={entry} value={entry}>
                {entry}
              </option>
            ))}
          </select>
        </span>
      </div>
      <div className={`${controls.field} ${controls.text}`}>
        <label htmlFor="f-joberror">Error code</label>
        <input
          id="f-joberror"
          name="error_code"
          type="text"
          defaultValue={values.error_code}
          placeholder="E_RATE_LIMIT"
          autoComplete="off"
        />
      </div>
      <div className={`${controls.field} ${controls.pickField}`}>
        <label htmlFor="f-joborder">Order</label>
        <span className={controls.pick}>
          <select id="f-joborder" name="order" defaultValue={values.order}>
            {ORDERS.map((entry) => (
              <option key={entry} value={entry}>
                {entry.replace("_", " ")}
              </option>
            ))}
          </select>
        </span>
      </div>
      <label className={controls.check}>
        <input type="checkbox" name="degraded" value="1" defaultChecked={values.degraded} />
        <span className={controls.checkWord}>Degraded only</span>
      </label>
      <div className={`${controls.field} ${controls.narrow}`}>
        <label htmlFor="f-joblimit">Rows</label>
        {/* No `max`: the ceiling is the server's, disclosed in `notes`. */}
        <input
          id="f-joblimit"
          name="limit"
          type="number"
          min={1}
          defaultValue={values.limit}
          inputMode="numeric"
        />
      </div>
      <div className={`${controls.field} ${controls.actions}`}>
        <button className={controls.button} type="submit">
          Apply
        </button>
        <DashLink className={controls.ghostlink} href={`${ROOT}/jobs`}>
          Reset
        </DashLink>
      </div>
    </FilterBand>
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
