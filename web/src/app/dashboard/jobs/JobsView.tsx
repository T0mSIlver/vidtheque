"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, type FormEvent } from "react";
import { dashboard, ROOT } from "@/lib/dashboard/client";
import type { JobCard, Jobs } from "@/lib/dashboard/schemas";
import { at, DASH } from "@/lib/format";
import dash from "../dashboard.module.css";
import { DashLink, Fact, PageHead, ReadFailure, Reading, Sep, Unbroken } from "../parts";
import { useJobsPoll } from "../useJobsPoll";
import { CancelControl } from "./CancelControl";
import styles from "./jobs.module.css";
import {
  countsOf,
  jobHeadline,
  JobStates,
  Progress,
  refusalOf,
  useWriteSide,
  WallClock,
} from "./parts";

// The jobs table — `templates/jobs.html`, reading `GET /dashboard/api/jobs` in
// the browser (dashboard.md §5.4, §16.3).
//
// The page exists for one moment: 03:00, sixteen videos in a pool, waiting and
// coming back, and nothing on any surface saying so. So the two facts a row
// carries that no other page has are the ones with the most weight on it —
// what the job is *held* by, counting down, and how long it has actually been
// alive, counting up.
//
// **The filters are the URL**, exactly as they were in Jinja and for the same
// reason: a filtered table is a link somebody can send, the back button walks
// the triages you actually ran, and there is one copy of the query rather than
// a component state to keep in step with it. Every bound is Python's:
// `list_jobs` owns every predicate and every ordering so the page and the poll
// target cannot disagree, and this side sends what the reader typed.

/** Every parameter the view takes, in the order the band asks them. */
const FILTERS = ["state", "kind", "error_code", "order", "degraded", "limit"] as const;

// `offset` is the pager's, not the band's: changing a filter changes the set,
// and page four of the old set is not page four of the new one.
const PAGE_KEYS = [...FILTERS, "offset"];

// `views._JOB_STATES`, `_JOB_KINDS` and `_JOB_ORDERS` — the words the pickers
// offer. They are the options of a control, not a bound: an unknown value is
// still sent, and the server falls back to its own default.
const STATES = ["all", "active", "failed", "done"];
const KINDS = ["all", "index", "reindex", "delete", "follow_check"];
const ORDERS = ["newest", "priority", "wall_clock"];

// A picker resting on the value the API would have used anyway. Sending it is
// not wrong, but it puts `&state=all&kind=all` on every link a reader copies
// out of the address bar to say nothing at all.
const DEFAULTS: Record<string, string> = { state: "all", kind: "all", order: "newest" };

export function JobsView() {
  const params = useSearchParams();
  const search = params.toString();
  // Keyed on the query string, which is the whole of this page's input: a new
  // URL is a new read, and nothing else re-runs it.
  const read = useCallback(
    (signal: AbortSignal) => dashboard.jobs(apiQuery(search), signal),
    [search],
  );
  const state = useJobsPoll(read);
  const data = state.data;

  return (
    <>
      <PageHead title="Jobs">
        {/* No separator in front of the cadence: `Narrowing` glues one to the
            end of every fact it prints, so a strip with nothing narrowing it
            would otherwise open on a middot. */}
        <Narrowing search={search} />
        {data ? (
          <Unbroken>
            <Fact label="refresh" value={`${Math.round(data.poll_ms / 1000)}s`} />
          </Unbroken>
        ) : null}
      </PageHead>

      <Filters search={search} limit={data?.pagination.limit} />

      {state.status === "loading" ? <Reading /> : null}
      {state.status === "failed" ? (
        <ReadFailure error={state.error} onRetry={state.reload} />
      ) : null}
      {data ? <Table data={data} search={search} stopped={state.error} /> : null}
    </>
  );
}

/** What is actually narrowing the table, on the title's own baseline.
 *
 *  The URL's values rather than an echo, because this payload has none: the
 *  videos table reads `filters` and `notes` back off its own, and
 *  `/dashboard/api/jobs` sends neither — so an unknown `state` the server fell
 *  back on is printed here as it was asked for. The one bound that *is* echoed
 *  is `limit`, and the Rows box shows it.
 *
 *  The order is deliberately not here: every entry is a *narrowing*, and an
 *  order takes no rows out. */
function Narrowing({ search }: { search: string }) {
  const params = new URLSearchParams(search);
  const value = (key: string) => params.get(key)?.trim() || "";

  const facts: [string, string][] = [];
  for (const key of ["state", "kind", "error_code"] as const) {
    const chosen = value(key);
    if (chosen && chosen !== DEFAULTS[key]) facts.push([key.replace("_", " "), chosen]);
  }
  if (value("degraded") === "1") facts.push(["degraded", "only"]);

  return (
    <>
      {facts.map(([label, text]) => (
        // The separator belongs to the fact before it, and the space after it
        // is written out: JSX drops whitespace that holds a newline, so
        // without it the strip has no break opportunity at all.
        <span key={label}>
          <Unbroken>
            <Fact label={label} value={text} />
            <Sep />
          </Unbroken>{" "}
        </span>
      ))}
    </>
  );
}

/**
 * The control band. A real form over the URL: submitting navigates, and the
 * page re-reads because its query string changed.
 *
 * Seeded with `defaultValue` and re-keyed on the query string, so the browser
 * owns what is being typed and a navigation reseeds every control from the URL
 * that arrived.
 */
function Filters({ search, limit }: { search: string; limit?: number }) {
  const router = useRouter();
  const params = new URLSearchParams(search);
  const value = (key: string, fallback = "") => params.get(key) ?? fallback;

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const next = new URLSearchParams();
    for (const key of FILTERS) {
      const entry = form.get(key);
      if (typeof entry !== "string") continue;
      const chosen = entry.trim();
      if (chosen && chosen !== DEFAULTS[key]) next.set(key, chosen);
    }
    router.push(next.toString() ? `${ROOT}/jobs?${next}` : `${ROOT}/jobs`);
  }

  return (
    <form className={dash.filters} key={search} onSubmit={submit}>
      <div className={`${dash.field} ${dash.pickField}`}>
        <label htmlFor="f-jobstate">State</label>
        <span className={dash.pick}>
          <select id="f-jobstate" name="state" defaultValue={value("state", "all")}>
            {STATES.map((entry) => (
              <option key={entry} value={entry}>
                {entry}
              </option>
            ))}
          </select>
        </span>
      </div>
      <div className={`${dash.field} ${dash.pickField}`}>
        <label htmlFor="f-jobkind">Kind</label>
        <span className={dash.pick}>
          <select id="f-jobkind" name="kind" defaultValue={value("kind", "all")}>
            {KINDS.map((entry) => (
              <option key={entry} value={entry}>
                {entry}
              </option>
            ))}
          </select>
        </span>
      </div>
      <div className={`${dash.field} ${dash.text}`}>
        <label htmlFor="f-joberror">Error code</label>
        <input
          id="f-joberror"
          name="error_code"
          type="text"
          defaultValue={value("error_code")}
          placeholder="E_RATE_LIMIT"
          autoComplete="off"
        />
      </div>
      <div className={`${dash.field} ${dash.pickField}`}>
        <label htmlFor="f-joborder">Order</label>
        <span className={dash.pick}>
          <select id="f-joborder" name="order" defaultValue={value("order", "newest")}>
            {ORDERS.map((entry) => (
              <option key={entry} value={entry}>
                {entry.replace("_", " ")}
              </option>
            ))}
          </select>
        </span>
      </div>
      <label className={dash.check}>
        <input
          type="checkbox"
          name="degraded"
          value="1"
          defaultChecked={value("degraded") === "1"}
        />
        <span className={dash.checkWord}>Degraded only</span>
      </label>
      <div className={`${dash.field} ${dash.narrow}`}>
        <label htmlFor="f-joblimit">Rows</label>
        {/* No `max`: the ceiling is `views.JOB_PAGE_MAX` and this page has no
            copy of it. The number the server accepted is the placeholder, so
            an empty box over a page of twenty-five still says how big a page
            is. */}
        <input
          id="f-joblimit"
          name="limit"
          type="number"
          min={1}
          defaultValue={value("limit")}
          placeholder={limit === undefined ? undefined : String(limit)}
          inputMode="numeric"
        />
      </div>
      <div className={`${dash.field} ${dash.actions}`}>
        <button className={dash.ghostlink} type="submit">
          Apply
        </button>
        <DashLink className={dash.ghostlink} href={`${ROOT}/jobs`}>
          Reset
        </DashLink>
      </div>
    </form>
  );
}

function Table({ data, search, stopped }: { data: Jobs; search: string; stopped: unknown }) {
  const { rendered } = useWriteSide();
  const rows = data.jobs;
  if (!rows.length) return <Empty search={search} />;

  return (
    <>
      <p className={dash.tablecount} role="status">
        <span>
          <span className={dash.shown}>{rows.length}</span> shown
          {data.pagination.has_more ? ", more available" : null}.{" "}
          {/* The tick is the whole of this page's liveness, so when it is no
              longer running the page says so rather than freezing quietly.
              Nothing is wrong when everything is terminal — there is simply
              nothing left to poll for. */}
          {stopped ? (
            <span className={styles.staleNote}>
              the live view stopped: {refusalOf(stopped).message}
            </span>
          ) : !data.live ? (
            <span className={styles.staleNote}>nothing is running, so this is a snapshot</span>
          ) : null}
        </span>
      </p>

      <div className={dash.tablewrap}>
        <table className={`${dash.grid} ${styles.jobs}`}>
          <caption className={dash.srOnly}>
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
              <th scope="col" className={dash.num}>
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
              <Row key={job.job_id} job={job} tickMs={data.poll_ms} actions={rendered} />
            ))}
          </tbody>
        </table>
      </div>

      <Pager pagination={data.pagination} search={search} />
    </>
  );
}

function Row({ job, tickMs, actions }: { job: JobCard; tickMs: number; actions: boolean }) {
  return (
    <tr>
      {/* What the job *contains*, not just what it is called: the row's
          headline is the first item's video title where the payload carries
          one, and the count of what went in where it does not. The id drops to
          the meta line beside the kind and the priority, with the other
          machine strings. The submitted URL is never here — §2.4 redacts it in
          the demo and the title it resolved to is corpus. */}
      <th scope="row" className={styles.colJob} data-label="Job">
        <DashLink className={dash.rowTitle} href={`${ROOT}/jobs/${encodeURIComponent(job.job_id)}`}>
          {jobHeadline(job)}
        </DashLink>
        {job.contents?.more ? (
          <span className={styles.rowMore}>+{job.contents.more} more</span>
        ) : null}
        <span className={dash.rowMeta}>
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
              <Sep /> <span className={styles.outcomeBad}>{job.degraded} degraded</span>
            </>
          ) : null}
        </span>
      </th>
      <td data-label="State">
        <span className={styles.colState}>
          <JobStates job={job} />
        </span>
      </td>
      <td data-label="Progress">
        <Progress job={job} tickMs={tickMs} />
      </td>
      <td data-label="Items">{countsOf(job)}</td>
      <td data-label="Created">
        <time className={dash.nowrap}>{at(job.created_at)}</time>
      </td>
      {/* The one clock on this row that arrives *while the page is open* — a
          job finishes between two readings — so it comes off the payload each
          tick. A job still running prints the em dash: this has not happened
          yet, and a blank cell would read as a value the page failed to
          fetch. */}
      <td data-label="Finished">
        <time className={dash.nowrap}>{job.finished_at ? at(job.finished_at) : DASH}</time>
      </td>
      <td className={dash.num} data-label="Wall clock">
        <WallClock seconds={job.wall_s} live={job.live} />
      </td>
      {actions ? (
        <td className={styles.colActions} data-label="Action">
          {job.live ? <CancelControl job={job} /> : <span className={dash.muted}>{DASH}</span>}
        </td>
      ) : null}
    </tr>
  );
}

function Pager({ pagination, search }: { pagination: Jobs["pagination"]; search: string }) {
  const { limit, offset, has_more } = pagination;
  if (!offset && !has_more) return null;
  return (
    <nav className={dash.pager} aria-label="Pagination">
      {offset ? (
        <DashLink
          className={dash.ghostlink}
          href={linkTo(search, { offset: String(Math.max(offset - limit, 0)) })}
        >
          ← Newer
        </DashLink>
      ) : null}
      {has_more ? (
        <DashLink
          className={dash.ghostlink}
          href={linkTo(search, { offset: String(offset + limit) })}
        >
          Older {limit} →
        </DashLink>
      ) : null}
    </nav>
  );
}

/** No rows, in the two ways that happens: a filter that matched nothing, and
 *  an instance that has never queued anything at all. */
function Empty({ search }: { search: string }) {
  const { rendered } = useWriteSide();
  const params = new URLSearchParams(search);
  const state = params.get("state")?.trim();
  const narrowed = Boolean(
    (state && state !== "all") ||
    params.get("kind")?.trim() ||
    params.get("error_code")?.trim() ||
    params.get("degraded")?.trim(),
  );

  return (
    <section className={dash.notice} aria-labelledby="nojobs">
      <h2 className={dash.noticeTitle} id="nojobs">
        No jobs to show.
      </h2>
      <p className={dash.noticeDetail}>
        {narrowed ? (
          <>
            The filters are narrowing it
            {state && state !== "all" ? (
              <>
                {" "}
                to <code>{state}</code>
              </>
            ) : null}
            .
          </>
        ) : (
          <>
            Nothing has ever been queued on this instance. <code>index-video</code> creates one.
          </>
        )}
      </p>
      <p className={dash.noticeNext}>
        {narrowed ? <DashLink href={`${ROOT}/jobs`}>Show every job</DashLink> : null}
        {narrowed && rendered ? <Sep /> : null}
        {rendered ? <DashLink href={`${ROOT}/index`}>Add videos</DashLink> : null}
      </p>
    </section>
  );
}

/** The page's URL, filtered down to the parameters the view takes.
 *
 *  A whitelist rather than a passthrough: this is the string that becomes a
 *  request, and an unknown key in the URL bar has no business reaching the API
 *  because somebody pasted it. Values go as typed — the clamps are Python's,
 *  and one corrected here would be a clamp the reader is never told about. */
export function apiQuery(search: string): URLSearchParams {
  const from = new URLSearchParams(search);
  const query = new URLSearchParams();
  for (const key of PAGE_KEYS) {
    const value = from.get(key);
    if (value !== null && value.trim()) query.set(key, value.trim());
  }
  return query;
}

/** This page's URL with some of its parameters changed; `null` removes one. */
function linkTo(search: string, changes: Record<string, string | null>): string {
  const next = apiQuery(search);
  for (const [key, value] of Object.entries(changes)) {
    if (value === null) next.delete(key);
    else next.set(key, value);
  }
  const query = next.toString();
  return query ? `${ROOT}/jobs?${query}` : `${ROOT}/jobs`;
}
