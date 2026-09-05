"use client";

import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { Pill } from "@/components/Pill";
import { dashboard, DashboardError, ROOT } from "@/lib/dashboard/client";
import type { FollowDetail, FollowJob, FollowRow, SeenRow } from "@/lib/dashboard/schemas";
import { at, count, DASH, day, duration, iso } from "@/lib/format";
import dash from "../../dashboard.module.css";
import {
  DashLink,
  Fact,
  PageHead,
  Panel,
  ReadFailure,
  Reading,
  Sep,
  Unbroken,
  useWriteSide,
} from "../../parts";
import { useSessionRead } from "../../session";
import { useRead } from "../../useRead";
import { Absent } from "../Absent";
import {
  CheckControl,
  DeleteControl,
  QueueControl,
  RulesDisclosure,
  StateControl,
} from "../Controls";
import styles from "../following.module.css";
import { nearMissLine, nextCheckWords, RuleFacts } from "../parts";

// One follow — `templates/follow.html`, reading
// `GET /dashboard/api/following/{slug}` in the browser (dashboard.md §18.4).
//
// Three bands in this order: the rule it obeys, the checks it has run, and what
// it passed over. The third is the point of the page. A follow that quietly
// drops a four-minute talk because its floor is eight would be the one place
// this index goes silent, and silence is the defect (PRODUCT.md, principle 3) —
// so every candidate carries the sentence with the number that made the
// decision, and a button to overrule it.
//
// There is no thumbnail anywhere below and there cannot be one: an un-indexed
// video has no keyframe on this disk, and a YouTube thumbnail URL would be a
// runtime request off this box. The ledger is text, and that is not a
// compromise — the reason is the evidence here, not the picture.
//
// **What this page does not render, and why.** The Jinja page opens with
// `follows.rules.describe` — the rule as one English sentence — and that
// function is still the only renderer of a policy as English (§22). It does not
// travel on this payload, so what stands at the top here is the same rule as
// facts, from the one formatter the table uses, one size up.

/** The two parameters the ledger's pager takes. */
const PAGE_KEYS = ["limit", "offset"];

export function FollowDetailView({ slug }: { slug: string }) {
  const params = useSearchParams();
  const search = params.toString();
  const query = apiQuery(search);
  const read = useCallback(
    (signal: AbortSignal) => dashboard.follow(slug, apiQuery(search), signal),
    [slug, search],
  );
  const state = useRead(read);
  const session = useSessionRead();
  const refusal = state.status === "failed" ? state.error : null;

  // The deployment registers no write side, so this page does not exist here —
  // the same fact §18.6 gives the list, arriving from `/api/session` or from
  // the endpoint's own `404`. An unknown *slug* is a different 404 and carries
  // the store's own code, so the two are told apart by the envelope.
  const gone = refusal instanceof DashboardError && refusal.status === 404;
  if ((session.status === "ready" && !session.data.write_side) || (gone && !isUnknownSlug(refusal)))
    return <Absent />;

  if (state.status === "loading") return <Reading />;

  if (state.status === "failed") {
    // A slug that is not a follow on this instance is not a failure to read
    // it: the read succeeded and the answer is "there is no such follow". It
    // gets the refusal's own words and a way back to the list, not a retry
    // button that would produce this answer again.
    if (gone) {
      const unknown = refusal as DashboardError;
      return (
        <>
          <Crumbs slug={slug} />
          <PageHead title="No such follow" />
          <section className={dash.notice} aria-labelledby="unknown">
            <h2 className={dash.noticeTitle} id="unknown">
              {unknown.message}
            </h2>
            <p className={dash.noticeDetail}>
              <code>{unknown.code}</code>
            </p>
            {unknown.next ? <p className={dash.noticeNext}>{unknown.next}</p> : null}
            <p className={dash.noticeNext}>
              <DashLink href={`${ROOT}/following`}>Back to Following</DashLink>
            </p>
          </section>
        </>
      );
    }
    return (
      <>
        <Crumbs slug={slug} />
        <PageHead title="Following" />
        <ReadFailure error={state.error} onRetry={state.reload} />
      </>
    );
  }

  return <Loaded initial={state.data} slug={slug} search={search} query={query} />;
}

function Loaded({
  initial,
  slug,
  search,
  query,
}: {
  initial: FollowDetail;
  slug: string;
  search: string;
  query: URLSearchParams;
}) {
  // A write answers with the row, and the page re-reads on top of it. Both,
  // deliberately: the row is the receipt the reader is owed *now*, and the
  // re-read is what catches the fields a write outcome has no room for — §21's
  // follow block carries no `last_error_code`, and a resume clears one.
  const [fresh, setFresh] = useState<FollowDetail | null>(null);
  const [written, setWritten] = useState<FollowRow | null>(null);
  const data = fresh ?? initial;
  const follow = { ...data.follow, ...(written ?? {}) };

  const catchUp = useCallback(
    (row: FollowRow) => {
      setWritten(row);
      dashboard
        .follow(slug, query)
        .then((next) => {
          setFresh(next);
          setWritten(null);
        })
        // A failed re-read leaves the row the write answered with on screen,
        // which is the truest thing this page holds either way.
        .catch(() => undefined);
    },
    [slug, query],
  );

  useEffect(() => {
    document.title = `${data.follow.title} — vidtheque`;
  }, [data.follow.title]);

  return (
    <>
      <Crumbs slug={slug} />

      <PageHead title={follow.title}>
        <span className={styles.headstates}>
          <Pill state={follow.state} />
          <Unbroken>
            <span>{follow.kind}</span>
          </Unbroken>
          <Unbroken>
            <Fact label="brought in" value={count(data.brought_in)} />
          </Unbroken>
        </span>
      </PageHead>

      <Rule data={data} follow={follow} onWritten={catchUp} />
      <Ledger data={data} />
      <PassedOver data={data} slug={slug} search={search} />
    </>
  );
}

function Crumbs({ slug }: { slug: string }) {
  return (
    <p className={styles.crumbs}>
      <DashLink href={`${ROOT}/following`}>← Following</DashLink> <span aria-hidden="true">/</span>{" "}
      <code>{slug}</code>
    </p>
  );
}

/**
 * Band 1 — the rule, and the clocks around it.
 *
 * The clocks are rows rather than figures: these are four readings compared
 * against each other, not four headlines. A clock set at the figure ramp would
 * out-shout the rule above it, which is the thing this band exists to have
 * read.
 */
function Rule({
  data,
  follow,
  onWritten,
}: {
  data: FollowDetail;
  follow: FollowDetail["follow"];
  onWritten: (row: FollowRow) => void;
}) {
  const { rendered } = useWriteSide();
  return (
    <Panel id="rule" title="The rule">
      <RuleFacts follow={follow} wide />

      <ul className={`${dash.rowlist} ${dash.tight}`}>
        <li className={dash.minirow}>
          <span>last check</span>
          <span className={dash.minirowFigure}>
            <time dateTime={iso(follow.last_check_at)}>{at(follow.last_check_at)}</time>
          </span>
        </li>
        <li className={dash.minirow}>
          <span>next check</span>
          <span className={dash.minirowFigure}>{nextCheckWords(follow)}</span>
        </li>
        <li className={dash.minirow}>
          <span>last arrival</span>
          <span className={dash.minirowFigure}>
            <time dateTime={iso(follow.last_new_at)}>{at(follow.last_new_at)}</time>
          </span>
        </li>
        <li className={dash.minirow}>
          <span>source</span>
          <span className={dash.minirowFigure}>
            <code>{follow.source_url}</code>
          </span>
        </li>
      </ul>

      {/* The last thing that went wrong, printed whether or not the state is
          `failing`: one rate limit does not fail a follow, and a reader looking
          at a green pill still wants to know what the last check hit. */}
      {data.follow.last_error_code ? (
        <p className={styles.lasterror}>
          <Pill state={data.follow.last_error_code} tone="bad" />
          <span className={styles.errText}>{data.follow.last_error_message ?? DASH}</span>
        </p>
      ) : null}

      {data.in_flight ? (
        <p className={styles.panelNote}>
          A check is already on the queue as{" "}
          <DashLink href={`${ROOT}/jobs/${encodeURIComponent(data.in_flight)}`}>
            <code>{data.in_flight}</code>
          </DashLink>
          .
        </p>
      ) : null}

      {/* Every control here POSTs, and each one is drawn only where the write
          routes are registered — which on this surface is everywhere the page
          is, since the page is registered with them. */}
      {rendered ? (
        <>
          <div className={styles.followactions}>
            <StateControl follow={follow} onWritten={onWritten} />
            {follow.state === "active" ? (
              <CheckControl follow={follow} onWritten={onWritten} />
            ) : null}
            <DeleteControl slug={follow.slug} />
          </div>
          <p className={styles.fieldHelp}>
            <em>Check now</em> makes the clock due; the queue claims the check on its next tick.{" "}
            <em>Unfollow</em> stops the checks and leaves every video this follow brought in — they
            are corpus, not membership.
          </p>
          <RulesDisclosure follow={data.follow} onWritten={onWritten} />
        </>
      ) : null}
    </Panel>
  );
}

/** Band 2 — this follow's own checks, and the index jobs they enqueued. Both
 *  carry a job id rather than a copy of the job: the war story is already
 *  written at `/dashboard/jobs/{job_id}` and this band does not fork it. */
function Ledger({ data }: { data: FollowDetail }) {
  return (
    <div className={dash.split}>
      <Panel id="checks" title="Recent checks">
        {data.checks.length ? (
          <ul className={`${dash.rowlist} ${dash.tight}`}>
            {data.checks.map((check) => (
              <JobRow job={check} key={check.job_id}>
                {duration(took(check))}
              </JobRow>
            ))}
          </ul>
        ) : (
          <p className={dash.emptyNote}>
            No check has run yet. The first one is queued on the next tick.
          </p>
        )}
        <p className={styles.panelNote}>
          The {data.caps.checks} most recent, bounded independently of the ledger&rsquo;s pager.
        </p>
      </Panel>

      <Panel id="queued" title="Jobs this follow queued">
        {data.index_jobs.length ? (
          <ul className={`${dash.rowlist} ${dash.tight}`}>
            {data.index_jobs.map((job) => (
              <JobRow job={job} key={job.job_id}>
                {job.n_done ?? 0}/{job.n_items ?? 0} done
                {job.n_failed ? `, ${job.n_failed} failed` : null}
              </JobRow>
            ))}
          </ul>
        ) : (
          <p className={dash.emptyNote}>Nothing has been queued from this follow.</p>
        )}
      </Panel>
    </div>
  );
}

function JobRow({ job, children }: { job: FollowJob; children: React.ReactNode }) {
  return (
    <li className={dash.minirow}>
      <DashLink href={`${ROOT}/jobs/${encodeURIComponent(job.job_id)}`}>
        <code>{job.job_id}</code>
      </DashLink>
      <Pill state={job.state} />
      {job.error_code ? <Pill state={job.error_code} tone="bad" /> : null}
      <span className={`${dash.minirowFigure} ${dash.muted}`}>
        <time dateTime={iso(job.created_at)}>{at(job.created_at)}</time>
      </span>
      <span className={dash.minirowFigure}>{children}</span>
    </li>
  );
}

/** How long a check took, from the two stamps it kept. `null` when either end
 *  is missing — a check that never finished has no duration, and "running for
 *  4 days" would be a guess. */
function took(job: FollowJob): number | null {
  if (!job.started_at || !job.finished_at) return null;
  return job.finished_at - job.started_at;
}

/**
 * Band 3 — what it passed over. The point of the page.
 *
 * Every decision except `queued`: a candidate the rule *accepted* is not one it
 * passed over. `reason` is printed verbatim — it was written where the decision
 * was made and it carries the number that made it, and "4:12, shorter than your
 * 8:00 floor" is a receipt where "too short" is an opinion.
 */
function PassedOver({ data, slug, search }: { data: FollowDetail; slug: string; search: string }) {
  const { rendered } = useWriteSide();
  return (
    <section className={dash.panel} id="passed" aria-labelledby="passedover">
      <h2 className={dash.panelTitle} id="passedover">
        What it passed over
      </h2>

      {/* One derived line, read out of the rows below and printed only when it
          is true. `near_miss` is `null` when the count is zero or the follow
          has no length rule, and `null` means print nothing: a "0 of the last
          25" line is a fact about nothing dressed as a finding. */}
      {data.near_miss ? <p className={styles.finding}>{nearMissLine(data.near_miss)}</p> : null}

      <Decisions counts={data.counts} />

      {data.notes.length ? (
        <ul className={dash.notes}>
          {data.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      ) : null}

      {data.seen.length ? (
        <>
          <p className={dash.tablecount} role="status">
            <span>
              <span className={dash.shown}>{data.seen.length}</span> shown
              {data.pagination.has_more ? ", more available" : null}.
            </span>
          </p>

          <div className={dash.tablewrap}>
            <table className={dash.grid}>
              <caption className={dash.srOnly}>
                Every candidate this follow decided not to index, and why
              </caption>
              <thead>
                <tr>
                  <th scope="col">candidate</th>
                  <th scope="col">decision</th>
                  <th scope="col" className={dash.num}>
                    length
                  </th>
                  <th scope="col">published</th>
                  <th scope="col">why</th>
                  {rendered ? (
                    <th scope="col" className={styles.colActions}>
                      action
                    </th>
                  ) : null}
                </tr>
              </thead>
              <tbody>
                {data.seen.map((item) => (
                  <SeenLine item={item} key={item.url} slug={slug} actions={rendered} />
                ))}
              </tbody>
            </table>
          </div>

          <Pager pagination={data.pagination} slug={slug} search={search} />

          <p className={styles.fieldHelp}>
            <em>Index anyway</em> queues that one video with this follow&rsquo;s own channels and
            tags, and expands nothing. The rule is unchanged — overruling it once is not editing it.
          </p>
        </>
      ) : (
        <p className={dash.emptyNote}>
          Nothing has been passed over. Every candidate a check has seen was accepted, or no check
          has run yet.
        </p>
      )}
    </section>
  );
}

/** Every decision this follow has made, as the store's own words with counts.
 *  One grouped query behind it, and `queued` is in it — the band below is the
 *  eight that are not, and the difference is the page's whole subject. */
function Decisions({ counts }: { counts: FollowDetail["counts"] }) {
  const entries = Object.entries(counts);
  if (!entries.length) return null;
  return (
    <p className={styles.decisions}>
      {entries.map(([decision, n]) => (
        <span className={styles.decision} key={decision}>
          <Pill state={decision} />
          <span className={styles.decisionN}>×{n}</span>
        </span>
      ))}
    </p>
  );
}

function SeenLine({ item, slug, actions }: { item: SeenRow; slug: string; actions: boolean }) {
  return (
    <tr>
      <th scope="row" data-label="Candidate">
        <span className={dash.rowTitle}>{item.title}</span>
        <span className={styles.rowMeta}>
          <code>{item.url}</code>
        </span>
      </th>
      {/* A provisional decision says so rather than looking terminal:
          `held_budget` is re-decided on the next check — that is the whole
          difference between a budget and a filter — and `held_review` is
          waiting on a person and will wait forever if nobody looks. */}
      <td className={styles.colDecision} data-label="Decision">
        <Pill state={item.decision} />
        {item.decision === "held_budget" ? (
          <span className={styles.rowMeta}>re-decided on the next check</span>
        ) : item.decision === "held_review" ? (
          <span className={styles.rowMeta}>waiting on you</span>
        ) : null}
      </td>
      <td className={`${dash.num} ${styles.colWhen}`} data-label="Length">
        {duration(item.duration_s)}
      </td>
      <td className={styles.colWhen} data-label="Published">
        <time dateTime={iso(item.published_at)}>{day(item.published_at)}</time>
      </td>
      <td className={styles.colWhy} data-label="Why">
        <p className={styles.reason}>{item.reason ?? DASH}</p>
        <span className={styles.rowMeta}>
          {item.judged_from === "probe" ? (
            <>
              judged from a probe
              <Sep />{" "}
            </>
          ) : null}
          <time dateTime={iso(item.decided_at)}>{at(item.decided_at)}</time>
        </span>
      </td>
      {actions ? (
        <td className={styles.colActions} data-label="Action">
          <QueueControl slug={slug} url={item.url} />
        </td>
      ) : null}
    </tr>
  );
}

function Pager({
  pagination,
  slug,
  search,
}: {
  pagination: FollowDetail["pagination"];
  slug: string;
  search: string;
}) {
  const { limit, offset, has_more } = pagination;
  if (!offset && !has_more) return null;
  const link = (next: number) => {
    const query = apiQuery(search);
    query.set("offset", String(next));
    return `${ROOT}/following/${encodeURIComponent(slug)}?${query}#passed`;
  };
  return (
    <nav className={dash.pager} aria-label="Pagination">
      {offset ? (
        <DashLink className={dash.ghostlink} href={link(Math.max(offset - limit, 0))}>
          ← Newer
        </DashLink>
      ) : null}
      {has_more ? (
        <DashLink className={dash.ghostlink} href={link(offset + limit)}>
          Older {limit} →
        </DashLink>
      ) : null}
    </nav>
  );
}

/** Is this 404 the store saying "no such follow", rather than the deployment
 *  saying "no such surface"? The envelope is what tells them apart: an unknown
 *  slug carries `E_UNKNOWN_FOLLOW`, and an unregistered route carries nothing
 *  this page can read. */
function isUnknownSlug(error: DashboardError): boolean {
  return error.code === "E_UNKNOWN_FOLLOW";
}

/** The ledger's own parameters, filtered off the page URL and sent as typed. */
export function apiQuery(search: string): URLSearchParams {
  const from = new URLSearchParams(search);
  const query = new URLSearchParams();
  for (const key of PAGE_KEYS) {
    const value = from.get(key);
    if (value !== null && value.trim()) query.set(key, value.trim());
  }
  return query;
}
