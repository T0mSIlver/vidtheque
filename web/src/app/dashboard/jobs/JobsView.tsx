"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, type FormEvent } from "react";
import { dashboard, ROOT } from "@/lib/dashboard/client";
import type { JobCard, Jobs } from "@/lib/dashboard/schemas";
import { at, DASH } from "@/lib/format";
import dash from "../dashboard.module.css";
import {
  DashLink,
  Fact,
  PageHead,
  ReadFailure,
  Reading,
  refusalOf,
  Sep,
  Unbroken,
  useWriteSide,
} from "../parts";
import { usePatchedRows } from "../polling";
import { useSession } from "../session";
import { useJobsPoll } from "../useJobsPoll";
import { CancelControl } from "./CancelControl";
import styles from "./jobs.module.css";
import { countsOf, jobHeadline, JobStates, Progress, WallClock } from "./parts";

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
        {/* No separator in front of the cadence: `Facts` glues one to the end
            of every entry it prints, so a strip with nothing on it would
            otherwise open on a middot. */}
        <Facts filters={data?.filters} />
        {data ? (
          <Unbroken>
            <Fact label="refresh" value={`${Math.round(data.poll_ms / 1000)}s`} />
          </Unbroken>
        ) : null}
      </PageHead>

      <Filters search={search} filters={data?.filters} limit={data?.pagination.limit} />

      {state.status === "loading" ? <Reading /> : null}
      {state.status === "failed" ? (
        <ReadFailure error={state.error} onRetry={state.reload} />
      ) : null}
      {/* Keyed on the query string: a new listing is a new set of rows, and the
          baseline the tick patches has to start again with it. */}
      {data ? (
        <Table key={search} data={data} stopped={state.error} polling={state.polling} />
      ) : null}
    </>
  );
}

/** What this listing ran with, on the title's own baseline.
 *
 *  The payload's `filters`, not the URL's words: `state=nonsense` falls back to
 *  `all` server-side, and a strip printing `state nonsense` over a table of
 *  every job would be the page vouching for a filter that never ran. The
 *  sentence saying it fell back is `notes`, printed above the table. Nothing is
 *  known until the first read lands, which is why this draws nothing at all
 *  while the page is still reading.
 *
 *  `state` and `order` are printed whether or not they narrow anything, which
 *  is `jobs.html`'s own strip: the table is read *in an order*, and an order
 *  nobody prints is an order nobody can tell has changed. The other three
 *  appear only when they take rows out — `kind all` is the absence of a filter
 *  said twice. */
function Facts({ filters }: { filters?: Jobs["filters"] }) {
  if (!filters) return null;
  const facts: [string, string][] = [["state", filters.state]];
  if (filters.kind !== DEFAULTS.kind) facts.push(["kind", filters.kind]);
  if (filters.error_code) facts.push(["error code", filters.error_code]);
  if (filters.degraded) facts.push(["degraded", "only"]);
  facts.push(["order", filters.order]);

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
 * **Seeded from the answer, not from the question.** `jobs.html` filled every
 * control from `filters` — the values the listing actually ran with — so a
 * `state=nonsense` that fell back showed `all`, an `error_code` truncated to 64
 * characters showed the 64, and the Rows box showed the page size the server
 * accepted rather than the 100000 somebody typed. A band echoing the URL is a
 * band claiming a filter ran that did not. The URL is the seed only until the
 * first read lands, because until then nothing has answered.
 *
 * Every control is uncontrolled and re-keyed on what it was seeded with, so the
 * browser owns what is being typed and a new reading reseeds the band.
 */
function Filters({
  search,
  filters,
  limit,
}: {
  search: string;
  filters?: Jobs["filters"];
  limit?: number;
}) {
  const router = useRouter();
  const params = new URLSearchParams(search);
  const asked = (key: string, fallback = "") => params.get(key) ?? fallback;
  const value = {
    state: filters?.state ?? asked("state", "all"),
    kind: filters?.kind ?? asked("kind", "all"),
    order: filters?.order ?? asked("order", "newest"),
    // `null` is the payload's word for "no error filter"; the form's is "".
    error_code: filters ? (filters.error_code ?? "") : asked("error_code"),
    degraded: filters ? filters.degraded : asked("degraded") === "1",
    limit: limit === undefined ? asked("limit") : String(limit),
  };
  const seed = [
    value.state,
    value.kind,
    value.order,
    value.error_code,
    value.degraded,
    value.limit,
  ].join("|");

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
    <form className={dash.filters} key={seed} onSubmit={submit}>
      <div className={`${dash.field} ${dash.pickField}`}>
        <label htmlFor="f-jobstate">State</label>
        <span className={dash.pick}>
          <select id="f-jobstate" name="state" defaultValue={value.state}>
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
          <select id="f-jobkind" name="kind" defaultValue={value.kind}>
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
          defaultValue={value.error_code}
          placeholder="E_RATE_LIMIT"
          autoComplete="off"
        />
      </div>
      <div className={`${dash.field} ${dash.pickField}`}>
        <label htmlFor="f-joborder">Order</label>
        <span className={dash.pick}>
          <select id="f-joborder" name="order" defaultValue={value.order}>
            {ORDERS.map((entry) => (
              <option key={entry} value={entry}>
                {entry.replace("_", " ")}
              </option>
            ))}
          </select>
        </span>
      </div>
      <label className={dash.check}>
        <input type="checkbox" name="degraded" value="1" defaultChecked={value.degraded} />
        <span className={dash.checkWord}>Degraded only</span>
      </label>
      <div className={`${dash.field} ${dash.narrow}`}>
        <label htmlFor="f-joblimit">Rows</label>
        {/* No `max`: the ceiling is `views.JOB_PAGE_MAX` and this page keeps
            no copy of it — a clamp that moved says so in `notes`, in the
            server's own words. What the box holds is the page size the server
            accepted, which is what `jobs.html` echoed here. */}
        <input
          id="f-joblimit"
          name="limit"
          type="number"
          min={1}
          defaultValue={value.limit}
          inputMode="numeric"
        />
      </div>
      <div className={`${dash.field} ${dash.actions}`}>
        <button className={dash.button} type="submit">
          Apply
        </button>
        <DashLink className={dash.ghostlink} href={`${ROOT}/jobs`}>
          Reset
        </DashLink>
      </div>
    </form>
  );
}

function Table({ data, stopped, polling }: { data: Jobs; stopped: unknown; polling: boolean }) {
  const { rendered } = useWriteSide();
  const { rows, arrived: queuedSince } = usePatchedRows(data.jobs, (job) => job.job_id);
  // The projection this listing ran under, off the listing itself. The session
  // is the fallback and only that: it is the same deployment answering, but it
  // is not the read that was taken, and an instance predating the field is the
  // only reason to ask it.
  const readonly = Boolean(useSession()?.readonly);
  const redacted = data.redacted ?? readonly;

  // Where a moved bound and a fallen-back filter are disclosed. The Jinja page
  // echoed an accepted `limit` back into the field the reader typed it into; a
  // payload has no form, so the sentence rides on `notes` — policy text,
  // rendered here and composed in Python. Above the empty state too: a listing
  // that answered a different question than the one asked has to say so
  // whether or not it found rows.
  const notes = data.notes.length ? (
    <ul className={dash.notes}>
      {data.notes.map((note) => (
        <li key={note}>{note}</li>
      ))}
    </ul>
  ) : null;

  // The tick patches the rows that were here; a job queued since cannot be
  // patched into existence, and the count line would stop being true if it
  // were. `jobs.js` revealed a note instead, and so does this — including on a
  // listing that loaded with nothing in it, which is the reading most likely
  // to grow a job under the reader and the one that has no count line to hang
  // the note on.
  const arrival = queuedSince ? (
    <span className={styles.staleNote}>a job was queued since this page loaded, so reload</span>
  ) : null;

  if (!rows.length) {
    return (
      <>
        {notes}
        {arrival ? (
          <p className={dash.tablecount} role="status">
            <span>{arrival}</span>
          </p>
        ) : null}
        <Empty filters={data.filters} />
      </>
    );
  }

  return (
    <>
      {notes}
      <p className={dash.tablecount} role="status">
        <span>
          <span className={dash.shown}>{rows.length}</span> shown
          {data.pagination.has_more ? ", more available" : null}.{" "}
          {/* The tick is the whole of this page's liveness, so when it is no
              longer running the page says so rather than freezing quietly.
              Nothing is wrong when everything is terminal — there is simply
              nothing left to poll for. */}
          {arrival}
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

      <Pager data={data} />

      {/* What this deployment does not publish — the one line a reader of the
          demo cannot get anywhere else (`jobs.html`, §2.4). */}
      {redacted ? (
        <p className={styles.panelNote}>
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
      {/* What the job *contains*, not just what it is called: the row's
          headline is the first item's video title where the payload carries
          one, and the count of what went in where it does not. The id drops to
          the meta line beside the kind and the priority, with the other
          machine strings. The submitted URL is never here — §2.4 redacts it in
          the demo and the title it resolved to is corpus. */}
      <th scope="row" className={styles.colJob} data-label="Job">
        <DashLink className={dash.rowTitle} href={`${ROOT}/jobs/${encodeURIComponent(job.job_id)}`}>
          {headline.muted ? <span className={dash.muted}>{headline.text}</span> : headline.text}
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
          <JobStates job={job} moving={polling} />
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
        <WallClock seconds={job.wall_s} live={job.live && polling} />
      </td>
      {actions ? (
        <td className={styles.colActions} data-label="Action">
          {job.live ? <CancelControl job={job} /> : <span className={dash.muted}>{DASH}</span>}
        </td>
      ) : null}
    </tr>
  );
}

function Pager({ data }: { data: Jobs }) {
  const { limit, offset, has_more } = data.pagination;
  if (!offset && !has_more) return null;
  return (
    <nav className={dash.pager} aria-label="Pagination">
      {offset ? (
        <DashLink className={dash.ghostlink} href={pageLink(data, Math.max(offset - limit, 0))}>
          ← Newer
        </DashLink>
      ) : null}
      {has_more ? (
        <DashLink className={dash.ghostlink} href={pageLink(data, offset + limit)}>
          Older {limit} →
        </DashLink>
      ) : null}
    </nav>
  );
}

/** No rows, in the two ways that happens: a filter that matched nothing, and
 *  an instance that has never queued anything at all.
 *
 *  Which of the two it is comes off the echo, for the strip's reason: a
 *  `state=nonsense` that fell back to `all` narrowed nothing, and blaming a
 *  filter that never ran for an empty instance is the wrong screen. */
function Empty({ filters }: { filters: Jobs["filters"] }) {
  const { rendered } = useWriteSide();
  // `jobs.html`'s own predicate, and the narrow one on purpose: `state` is the
  // filter that empties this table, and "the filters are narrowing it" over a
  // listing narrowed only by `kind=delete` sends the reader to look for a
  // filter they would then have to find.
  const narrowed = filters.state !== DEFAULTS.state;

  return (
    <section className={dash.notice} aria-labelledby="nojobs">
      <h2 className={dash.noticeTitle} id="nojobs">
        No jobs to show.
      </h2>
      <p className={dash.noticeDetail}>
        {narrowed ? (
          <>
            The filter is on <code>{filters.state}</code>.
          </>
        ) : (
          <>
            Nothing has ever been queued on this instance. <code>index-video</code> creates one.
          </>
        )}
      </p>
      <p className={dash.noticeNext}>
        {/* `state=all` rather than a bare `/jobs`: the link that empties the
            one filter this screen is about, not the one that quietly discards
            everything else the reader typed. */}
        {narrowed ? <DashLink href={`${ROOT}/jobs?state=all`}>Show every job</DashLink> : null}
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

/** A page of this listing, as a link somebody can send.
 *
 *  All six parameters, from the payload rather than from the URL — `jobs.html`'s
 *  `page_link` macro, which spelled the whole query out. Page four of a listing
 *  is only page four of *that* listing, so a pager link that carried the offset
 *  and left the predicates to a default would be a link to a different table.
 */
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
