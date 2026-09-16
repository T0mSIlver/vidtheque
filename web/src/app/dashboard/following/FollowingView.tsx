"use client";

import { useSearchParams } from "next/navigation";
import { Pill } from "@/components/Pill";
import { dashboard, DashboardError, ROOT } from "@/lib/dashboard/client";
import { pick } from "@/lib/dashboard/query";
import { useResource } from "@/lib/dashboard/resource";
import type { FollowListRow, Following } from "@/lib/dashboard/schemas";
import { at, count, DASH, duration, iso } from "@/lib/format";
import { Absent, Notice, ReadFailure } from "../kit/notice";
import { Notes, Pager, table, TableCount } from "../kit/table";
import { DashLink, Fact, Figure, PageHead, Pending, ui, Unbroken } from "../kit/ui";
import { useSessionResource } from "../session";
import { AddForm } from "./AddForm";
import styles from "./following.module.css";
import { nextCheckWords, RetryFact, RuleFacts, schedulable, windowWords } from "./parts";

// The follows table (dashboard.md §18.3, §22): what the instance watches, what
// it held back, and the budget. Both reads are registered with the writes, so
// a deployment without a write side has no page here at all (§18.6).

const PAGE_KEYS = ["limit", "offset"];

export function FollowingView() {
  const params = useSearchParams();
  const query = pick(params, PAGE_KEYS);
  const following = useResource(`following?${query}`, (signal) =>
    dashboard.following(query, signal),
  );
  const session = useSessionResource();
  const data = following.data;
  const error = following.error;

  if (
    session.data?.write_side === false ||
    (error instanceof DashboardError && error.status === 404)
  ) {
    return <FollowingAbsent />;
  }

  return (
    <>
      <PageHead title="Following">
        <Unbroken>
          <Fact label="follows" value={data ? count(data.totals.follows) : DASH} />
        </Unbroken>
      </PageHead>

      {data ? (
        <Loaded data={data} onMade={following.reload} />
      ) : error !== undefined ? (
        <ReadFailure error={error} onRetry={following.reload} />
      ) : (
        <Pending />
      )}
    </>
  );
}

export function FollowingAbsent() {
  return (
    <Absent title="Following" heading="This deployment does not follow channels.">
      Following is part of the write side, and this instance registers none — it is either a
      read-only projection of somebody&rsquo;s index, or an instance with no credential configured
      to check. Nothing is disabled here: the rules, the checks and the ledger are not on this box
      at all.
    </Absent>
  );
}

function Loaded({ data, onMade }: { data: Following; onMade: () => void }) {
  const rows = data.follows;
  return (
    <>
      <Band data={data} />
      {data.checks_enabled ? null : <ChecksOff />}
      <Held data={data} />
      <Notes notes={data.notes} />
      {rows.length ? <Table rows={rows} data={data} /> : null}
      <AddForm follows={rows.length} vectorsReason={data.vectors_reason ?? null} onMade={onMade} />
    </>
  );
}

/** Six figures; a zero wears no tone. */
function Band({ data }: { data: Following }) {
  const { totals, budget } = data;
  const window = windowWords(budget.window_s);
  return (
    <section aria-labelledby="band">
      <h2 className={ui.srOnly} id="band">
        What this instance is watching
      </h2>
      <dl className={`${ui.ledger} ${ui.ledger6}`}>
        <Figure label="follows" notes={["channels and playlists watched"]}>
          {count(totals.follows)}
        </Figure>
        <Figure
          label="active"
          notes={[
            <>
              <Pill state={`${totals.paused} paused`} tone={totals.paused ? "wait" : "neutral"} />{" "}
              <Pill state={`${totals.failing} failing`} tone={totals.failing ? "bad" : "neutral"} />
            </>,
          ]}
        >
          {count(totals.active)}
        </Figure>
        <Figure label="brought in" notes={["videos a check accepted"]}>
          {count(totals.brought_in)}
        </Figure>
        <Figure label="held" notes={["waiting on you or on the budget"]}>
          {count(totals.held)}
        </Figure>
        <Figure
          label="due within the hour"
          notes={["follows a check will pick up, whose clock comes round"]}
        >
          {count(totals.due_soon)}
        </Figure>
        {/* Hours of video, rolling, across every follow together. */}
        <Figure
          label="budget"
          notes={[
            budget.ceiling_h
              ? `of ${budget.ceiling_h}h, over the last ${window}`
              : // `0` is the ceiling turned off, not "no budget left".
                `used in the last ${window}; no ceiling is set`,
          ]}
        >
          {duration(budget.spent_s)}
        </Figure>
      </dl>
    </section>
  );
}

/** With checks off every clock below is a time nothing happens at (§22). */
function ChecksOff() {
  return (
    <Notice
      id="checksoff"
      title="Follow checks are off on this instance."
      detail={
        <>
          Nothing claims a <code>follow_check</code>, so no rule below is running and no clock below
          is due. The rules and their ledgers are unchanged, and <em>Check now</em> still only moves
          a clock.
        </>
      }
    />
  );
}

/** Candidates waiting on a person, each with its door; warn, because nothing
 *  failed and nothing moves until somebody decides. */
function Held({ data }: { data: Following }) {
  const shown = data.held.length;
  if (!shown) return null;
  return (
    <Notice
      id="waiting"
      tone="warn"
      title={
        data.held_more
          ? `More than ${shown} videos are waiting for you.`
          : `${shown} ${shown === 1 ? "video is" : "videos are"} waiting for you.`
      }
      next={
        data.held_more ? (
          <>
            More are held than the {data.held_cap} listed here; each follow&rsquo;s own page has its
            whole ledger.
          </>
        ) : null
      }
    >
      <ul className={`${ui.rowlist} ${ui.tight}`}>
        {data.held.map((item) => (
          <li className={ui.minirow} key={`${item.slug}-${item.url}`}>
            <DashLink href={`${ROOT}/following/${encodeURIComponent(item.slug)}#passed`}>
              {item.title}
            </DashLink>
            <span className={`${ui.minirowFigure} ${ui.muted}`}>{item.follow}</span>
          </li>
        ))}
      </ul>
    </Notice>
  );
}

function Table({ rows, data }: { rows: FollowListRow[]; data: Following }) {
  const { limit, offset, has_more } = data.pagination;
  return (
    <>
      <TableCount shown={rows.length} hasMore={has_more} />

      <div className={table.tablewrap}>
        <table className={`${table.grid} ${styles.follows}`}>
          <caption className={ui.srOnly}>
            Every followed channel, its rule, its state and its clocks
          </caption>
          <thead>
            <tr>
              <th scope="col">channel</th>
              <th scope="col">rule</th>
              <th scope="col">state</th>
              <th scope="col">last check</th>
              <th scope="col">last arrival</th>
              <th scope="col">next check</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((follow) => (
              <Row key={follow.slug} follow={follow} checksEnabled={data.checks_enabled} />
            ))}
          </tbody>
        </table>
      </div>

      {/* The accepted page size rides on both links (`page_link`). */}
      <Pager
        limit={limit}
        offset={offset}
        hasMore={has_more}
        href={(next) =>
          `${ROOT}/following?${new URLSearchParams({ limit: String(limit), offset: String(next) })}`
        }
      />
    </>
  );
}

function Row({ follow, checksEnabled }: { follow: FollowListRow; checksEnabled: boolean }) {
  return (
    <tr>
      <th scope="row" data-label="Channel">
        {/* The slug when there is no name, or the link has no text. */}
        <DashLink
          className={ui.rowTitle}
          href={`${ROOT}/following/${encodeURIComponent(follow.slug)}`}
        >
          {follow.title || follow.slug}
        </DashLink>
        <span className={ui.rowMeta}>{follow.kind}</span>
      </th>
      <td className={styles.colRule} data-label="Rule">
        <RuleFacts follow={follow} />
      </td>
      <td data-label="State">
        <Pill state={follow.state} />
        {follow.state === "failing" ? <RetryFact follow={follow} /> : null}
        {/* Printed whatever the state: one rate limit does not fail a follow. */}
        {follow.last_error_code ? <Pill state={follow.last_error_code} tone="bad" /> : null}
      </td>
      <td data-label="Last check">
        <time className={ui.nowrap}>{at(follow.last_check_at)}</time>
      </td>
      <td data-label="Last arrival">
        <time className={ui.nowrap}>{at(follow.last_new_at)}</time>
      </td>
      <td data-label="Next check">
        <time
          className={ui.nowrap}
          dateTime={
            checksEnabled && schedulable(follow) && follow.next_check_at
              ? iso(follow.next_check_at)
              : undefined
          }
        >
          {nextCheckWords(follow, checksEnabled)}
        </time>
      </td>
    </tr>
  );
}
