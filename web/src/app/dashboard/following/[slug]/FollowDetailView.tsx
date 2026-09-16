"use client";

import { useSearchParams } from "next/navigation";
import type { ReactNode } from "react";
import { Pill } from "@/components/Pill";
import { dashboard, DashboardError, ROOT } from "@/lib/dashboard/client";
import { pick } from "@/lib/dashboard/query";
import { useResource } from "@/lib/dashboard/resource";
import type { FollowDetail, FollowJob, SeenRow } from "@/lib/dashboard/schemas";
import { at, count, DASH, day, duration, iso } from "@/lib/format";
import { notice, ReadFailure, Refusal } from "../../kit/notice";
import { Crumbs, Notes, Pager, table, TableCount } from "../../kit/table";
import { DashLink, Fact, PageHead, Panel, Pending, Sep, Title, ui, Unbroken } from "../../kit/ui";
import { useSessionResource } from "../../session";
import {
  CheckControl,
  DeleteControl,
  QueueControl,
  RulesDisclosure,
  StateControl,
} from "../Controls";
import { FollowingAbsent } from "../FollowingView";
import styles from "../following.module.css";
import { nearMissLine, nextCheckWords, RetryFact, RuleFacts } from "../parts";

// One follow (dashboard.md §18.4): the rule, the checks it ran, and what it
// passed over — the point of the page, each candidate with the reason that
// decided it and a button to overrule it.

const PAGE_KEYS = ["limit", "offset"];

export function FollowDetailView({ slug }: { slug: string }) {
  const params = useSearchParams();
  const query = pick(params, PAGE_KEYS);
  const follow = useResource(`follow:${slug}?${query}`, (signal) =>
    dashboard.follow(slug, query, signal),
  );
  const session = useSessionResource();
  const data = follow.data;
  const error = follow.error;

  // An unknown slug carries the store's code; an unregistered route carries none.
  const gone = error instanceof DashboardError && error.status === 404;
  const unknown = gone && error.code === "E_UNKNOWN_FOLLOW";
  if (session.data?.write_side === false || (gone && !unknown)) return <FollowingAbsent />;

  if (unknown) {
    return (
      <>
        <Crumbs section="following" label="← Following" id={slug} />
        <Refusal
          code={error.code}
          message={error.message}
          next={error.next}
          back={{ href: `${ROOT}/following`, label: "Following" }}
        />
      </>
    );
  }

  return (
    <>
      {/* The slug stands in until the read has the follow's name. */}
      <Title>{data?.follow.title || slug}</Title>
      <Crumbs section="following" label="← Following" id={slug} />
      {data ? (
        <Loaded data={data} slug={slug} onWritten={follow.reload} />
      ) : (
        <>
          <PageHead title={slug}>
            <Unbroken>
              <Fact label="brought in" value={DASH} />
            </Unbroken>
          </PageHead>
          {error !== undefined ? (
            <ReadFailure error={error} onRetry={follow.reload} />
          ) : (
            <Pending />
          )}
        </>
      )}
    </>
  );
}

function Loaded({
  data,
  slug,
  onWritten,
}: {
  data: FollowDetail;
  slug: string;
  onWritten: () => void;
}) {
  const follow = data.follow;
  return (
    <>
      <PageHead title={follow.title || slug}>
        <span className={styles.headstates}>
          <Pill state={follow.state} />
          {follow.state === "failing" ? (
            <Unbroken>
              <RetryFact follow={follow} wide />
            </Unbroken>
          ) : null}
          <Unbroken>
            <span>{follow.kind}</span>
          </Unbroken>
          <Unbroken>
            <Fact label="brought in" value={count(data.brought_in)} />
          </Unbroken>
        </span>
      </PageHead>

      <Rule data={data} onWritten={onWritten} />
      <Ledger data={data} />
      <PassedOver data={data} slug={slug} onWritten={onWritten} />
    </>
  );
}

/** The rule, and its clocks as rows compared against each other. */
function Rule({ data, onWritten }: { data: FollowDetail; onWritten: () => void }) {
  const follow = data.follow;
  return (
    <Panel id="rule" title="The rule">
      <RuleFacts follow={follow} wide />

      <ul className={`${ui.rowlist} ${ui.tight}`}>
        <li className={ui.minirow}>
          <span>last check</span>
          <span className={ui.minirowFigure}>
            <time dateTime={iso(follow.last_check_at)}>{at(follow.last_check_at)}</time>
          </span>
        </li>
        <li className={ui.minirow}>
          <span>next check</span>
          <span className={ui.minirowFigure}>{nextCheckWords(follow, data.checks_enabled)}</span>
        </li>
        <li className={ui.minirow}>
          <span>last arrival</span>
          <span className={ui.minirowFigure}>
            <time dateTime={iso(follow.last_new_at)}>{at(follow.last_new_at)}</time>
          </span>
        </li>
        <li className={ui.minirow}>
          <span>source</span>
          <span className={ui.minirowFigure}>
            <code>{follow.source_url}</code>
          </span>
        </li>
      </ul>

      {/* Printed whatever the state; a resume clears it with the row. */}
      {follow.last_error_code ? (
        <p className={styles.lasterror}>
          <Pill state={follow.last_error_code} tone="bad" />
          <span className={styles.errText}>{follow.last_error_message ?? DASH}</span>
        </p>
      ) : null}

      {/* So Check now cannot look like it did nothing; with checks off the job
          waits (§22). */}
      {data.in_flight ? (
        <p className={notice.panelNote}>
          A check is already on the queue as{" "}
          <DashLink href={`${ROOT}/jobs/${encodeURIComponent(data.in_flight)}`}>
            <code>{data.in_flight}</code>
          </DashLink>
          {data.checks_enabled ? "." : ", and nothing will claim it while checks are off."}
        </p>
      ) : null}

      <div className={styles.followactions}>
        <StateControl follow={follow} onWritten={onWritten} />
        {/* A paused follow has no clock to make due. */}
        {follow.state !== "paused" ? <CheckControl follow={follow} onWritten={onWritten} /> : null}
        <DeleteControl slug={follow.slug} />
      </div>
      <p className={styles.fieldHelp}>
        <em>Check now</em> makes the clock due; the queue claims the check on its next tick.{" "}
        <em>Unfollow</em> stops the checks and leaves every video this follow brought in — they are
        corpus, not membership.
      </p>
      <RulesDisclosure follow={follow} onWritten={onWritten} />
    </Panel>
  );
}

/** This follow's checks and the index jobs they queued, as ids and states. */
function Ledger({ data }: { data: FollowDetail }) {
  return (
    <div className={ui.split}>
      <Panel id="checks" title="Recent checks">
        {data.checks.length ? (
          <ul className={`${ui.rowlist} ${ui.tight}`}>
            {data.checks.map((check) => (
              <JobRow job={check} key={check.job_id}>
                {duration(took(check))}
              </JobRow>
            ))}
          </ul>
        ) : (
          <p className={ui.emptyNote}>
            No check has run yet. The first one is queued on the next tick.
          </p>
        )}
        <p className={notice.panelNote}>
          The {data.caps.checks} most recent, bounded independently of the ledger&rsquo;s pager.
        </p>
      </Panel>

      <Panel id="queued" title="Jobs this follow queued">
        {data.index_jobs.length ? (
          <ul className={`${ui.rowlist} ${ui.tight}`}>
            {data.index_jobs.map((job) => (
              <JobRow job={job} key={job.job_id}>
                {job.n_done ?? 0}/{job.n_items ?? 0} done
                {job.n_failed ? `, ${job.n_failed} failed` : null}
              </JobRow>
            ))}
          </ul>
        ) : (
          <p className={ui.emptyNote}>Nothing has been queued from this follow.</p>
        )}
      </Panel>
    </div>
  );
}

function JobRow({ job, children }: { job: FollowJob; children: ReactNode }) {
  return (
    <li className={ui.minirow}>
      <DashLink href={`${ROOT}/jobs/${encodeURIComponent(job.job_id)}`}>
        <code>{job.job_id}</code>
      </DashLink>
      <Pill state={job.state} />
      {job.error_code ? <Pill state={job.error_code} tone="bad" /> : null}
      <span className={`${ui.minirowFigure} ${ui.muted}`}>
        <time dateTime={iso(job.created_at)}>{at(job.created_at)}</time>
      </span>
      <span className={ui.minirowFigure}>{children}</span>
    </li>
  );
}

/** A check's duration from its two stamps, or `null` rather than a guess. */
function took(job: FollowJob): number | null {
  if (!job.started_at || !job.finished_at) return null;
  return job.finished_at - job.started_at;
}

/** Every decision but `queued`, with `reason` verbatim. */
function PassedOver({
  data,
  slug,
  onWritten,
}: {
  data: FollowDetail;
  slug: string;
  onWritten: () => void;
}) {
  const { limit, offset, has_more } = data.pagination;
  return (
    <section className={ui.panel} id="passed" aria-labelledby="passedover">
      <h2 className={ui.panelTitle} id="passedover">
        What it passed over
      </h2>

      {data.near_miss ? <p className={styles.finding}>{nearMissLine(data.near_miss)}</p> : null}

      <Decisions counts={data.counts} />
      <Notes notes={data.notes} />

      {data.seen.length ? (
        <>
          <TableCount shown={data.seen.length} hasMore={has_more} />

          <div className={table.tablewrap}>
            <table className={`${table.grid} ${styles.seen}`}>
              <caption className={ui.srOnly}>
                Every candidate this follow decided not to index, and why
              </caption>
              <thead>
                <tr>
                  <th scope="col">candidate</th>
                  <th scope="col">decision</th>
                  <th scope="col" className={table.num}>
                    length
                  </th>
                  <th scope="col">published</th>
                  <th scope="col">why</th>
                  <th scope="col" className={styles.colActions}>
                    action
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.seen.map((item) => (
                  <SeenLine item={item} key={item.url} slug={slug} onWritten={onWritten} />
                ))}
              </tbody>
            </table>
          </div>

          <Pager
            limit={limit}
            offset={offset}
            hasMore={has_more}
            href={(next) =>
              `${ROOT}/following/${encodeURIComponent(slug)}?${new URLSearchParams({
                limit: String(limit),
                offset: String(next),
              })}#passed`
            }
            previous="← Newer"
            next={`Older ${limit} →`}
          />

          <p className={styles.fieldHelp}>
            <em>Index anyway</em> queues that one video with this follow&rsquo;s own channels and
            tags, and expands nothing. The rule is unchanged — overruling it once is not editing it.
          </p>
        </>
      ) : (
        <p className={ui.emptyNote}>
          Nothing has been passed over. Every candidate a check has seen was accepted, or no check
          has run yet.
        </p>
      )}
    </section>
  );
}

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

function SeenLine({
  item,
  slug,
  onWritten,
}: {
  item: SeenRow;
  slug: string;
  onWritten: () => void;
}) {
  return (
    <tr>
      <th scope="row" data-label="Candidate">
        <span className={ui.rowTitle}>{item.title}</span>
        <span className={styles.rowMeta}>
          <code>{item.url}</code>
        </span>
      </th>
      {/* A provisional decision says so rather than looking terminal. */}
      <td className={styles.colDecision} data-label="Decision">
        <Pill state={item.decision} />
        {item.decision === "held_budget" ? (
          <span className={styles.rowMeta}>re-decided on the next check</span>
        ) : item.decision === "held_review" ? (
          <span className={styles.rowMeta}>waiting on you</span>
        ) : null}
      </td>
      <td className={`${table.num} ${styles.colWhen}`} data-label="Length">
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
      <td className={styles.colActions} data-label="Action">
        <QueueControl slug={slug} url={item.url} onWritten={onWritten} />
      </td>
    </tr>
  );
}
