"use client";

import { useSearchParams } from "next/navigation";
import { useCallback, useRef, useState } from "react";
import { Pill } from "@/components/Pill";
import { dashboard, DashboardError, ROOT } from "@/lib/dashboard/client";
import type { FollowCreated, FollowListRow, Following } from "@/lib/dashboard/schemas";
import { at, count, duration } from "@/lib/format";
import dash from "../dashboard.module.css";
import {
  DashLink,
  Fact,
  Figure,
  PageHead,
  Panel,
  ReadFailure,
  Reading,
  refusalOf,
  Unbroken,
  useWrite,
  useWriteSide,
} from "../parts";
import { useSessionRead } from "../session";
import { useRead } from "../useRead";
import { Absent } from "./Absent";
import styles from "./following.module.css";
import { nextCheckWords, RuleFacts, RuleFields, RuleForm, ruleValues, windowWords } from "./parts";

// The follows table — `templates/following.html`, reading
// `GET /dashboard/api/following` in the browser (dashboard.md §18.3, §22).
//
// A follow is the one thing on this instance that acts while nobody is
// watching, so this page's job is not "configure a follow": it is *show me what
// they did, and what they decided not to do*. That is why the band counts what
// is held as well as what arrived, why the budget is on this page and nowhere
// else, and why the one band addressed to a person — the candidates waiting on
// a human — sits above the table rather than inside it.
//
// **The whole surface can be absent.** Both `GET`s are registered with the
// write routes, so a deployment with no write side answers `404` on the page
// and on the JSON alike (§18.6). The shell has already asked `/api/session`,
// so this renders the designed absent state rather than a failed read — and a
// `404` from the list is the same answer arriving from the other direction.

/** The two parameters the view takes. Nothing else reaches the API because
 *  somebody pasted it into the address bar. */
const PAGE_KEYS = ["limit", "offset"];

export function FollowingView() {
  const params = useSearchParams();
  const search = params.toString();
  const read = useCallback(
    (signal: AbortSignal) => dashboard.following(apiQuery(search), signal),
    [search],
  );
  const state = useRead(read);
  const session = useSessionRead();

  // Two ways to learn one fact, and either is enough: the deployment says it
  // registers no write side, or the endpoint that is registered *with* the
  // writes answered `404`. The session is the faster of the two and the only
  // one that can answer before this page's own read lands.
  const refusal = state.status === "failed" ? state.error : null;
  const absent =
    (session.status === "ready" && !session.data.write_side) ||
    (refusal instanceof DashboardError && refusal.status === 404);
  if (absent) return <Absent />;

  return (
    <>
      <PageHead title="Following">
        {/* One fact, not two. The budget is a figure in the band directly
            under this line, and a copy of it up here would be the same number
            twice on the one row of the page that cannot wrap. */}
        {state.status === "ready" ? (
          <Unbroken>
            <Fact label="follows" value={count(state.data.totals.follows)} />
          </Unbroken>
        ) : null}
      </PageHead>

      {state.status === "loading" ? <Reading /> : null}
      {state.status === "failed" ? (
        <ReadFailure error={state.error} onRetry={state.reload} />
      ) : null}
      {state.status === "ready" ? <Loaded data={state.data} search={search} /> : null}
    </>
  );
}

function Loaded({ data, search }: { data: Following; search: string }) {
  // Rows the add form made, in front of the page the server sent. A created
  // follow is not on the payload this page has already read, and re-reading
  // here would blank the receipt the reader was just handed.
  const [made, setMade] = useState<FollowListRow[]>([]);
  const known = new Set(data.follows.map((row) => row.slug));
  const rows = [...made.filter((row) => !known.has(row.slug)), ...data.follows];

  return (
    <>
      <Band data={data} />
      {data.checks_enabled ? null : <ChecksOff />}
      <Held data={data} />

      {/* Where a clamp is disclosed. A payload has no form to echo an accepted
          `limit` back into, so the sentence rides on `notes` — policy text,
          composed in Python and rendered here. */}
      {data.notes.length ? (
        <ul className={dash.notes}>
          {data.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      ) : null}

      {rows.length ? <Table rows={rows} data={data} search={search} /> : null}

      <AddForm
        follows={rows.length}
        vectors={data.vectors}
        onMade={(row) => setMade((rows) => [row, ...rows])}
      />
    </>
  );
}

/**
 * The band: six figures, and every state printed as its own word.
 *
 * A follow that is paused and a follow that is failing are two different
 * mornings, and the colour reinforces the word rather than replacing it
 * (DESIGN.md, the Word-and-Colour Rule). A zero wears no tone, because a tone
 * on a zero is a warning about nothing.
 */
function Band({ data }: { data: Following }) {
  const { totals, budget } = data;
  const window = windowWords(budget.window_s);
  return (
    <section aria-labelledby="band">
      <h2 className={dash.srOnly} id="band">
        What this instance is watching
      </h2>
      <dl className={`${dash.ledger} ${dash.ledger6}`}>
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
        <Figure label="due within the hour" notes={["active follows whose clock comes round"]}>
          {count(totals.due_soon)}
        </Figure>
        {/* Hours of *video*, not GPU-minutes: a check knows a candidate's
            length before it knows what indexing it will cost, and hours of
            video is the number an operator reasons about. Rolling and across
            every follow together — a per-follow accounting would let five
            follows spend five budgets. */}
        <Figure
          label="budget"
          notes={[
            budget.ceiling_h
              ? `of ${budget.ceiling_h}h, over the last ${window}`
              : // A ceiling of `0` is the operator turning it off. "of 0h"
                // reads as "no budget left" and means the opposite.
                `used in the last ${window}; no ceiling is set`,
          ]}
        >
          {duration(budget.spent_s)}
        </Figure>
      </dl>
    </section>
  );
}

/**
 * The deployment fact that decides what every clock under it means (Tom,
 * 2026-09-05).
 *
 * With follow checks off, each `next_check_at` in the table is a time at which
 * nothing will happen — so the column prints the fact instead of the time
 * (`nextCheckWords`) and this line explains it once. It is in the list's voice
 * rather than the rail's because it is about *these rows*: the rail says what
 * the deployment may do, and this says what it will not.
 */
function ChecksOff() {
  return (
    <section className={dash.notice} aria-labelledby="checksoff">
      <h2 className={dash.noticeTitle} id="checksoff">
        Follow checks are off on this instance.
      </h2>
      <p className={dash.noticeDetail}>
        Nothing claims a <code>follow_check</code>, so no rule below is running and no clock below
        is due. The rules and their ledgers are unchanged, and <em>Check now</em> still only moves a
        clock.
      </p>
    </section>
  );
}

/** The one band addressed to a person rather than describing the instance:
 *  something matched a rule and is waiting for a human. It names the rows and
 *  gives each one its door, because "N are waiting" with nowhere to go is a
 *  notification and not a control. */
function Held({ data }: { data: Following }) {
  const shown = data.held.length;
  if (!shown) return null;
  return (
    <section className={dash.notice} aria-labelledby="waiting">
      {/* The count is this band's own rows and not the band figure above it:
          `held` up there is everything held, and the budget holds a candidate
          without asking anybody. These are the ones waiting on a *person*. */}
      <h2 className={dash.noticeTitle} id="waiting">
        {data.held_more
          ? `More than ${shown} videos are waiting for you.`
          : `${shown} ${shown === 1 ? "video is" : "videos are"} waiting for you.`}
      </h2>
      <ul className={`${dash.rowlist} ${dash.tight}`}>
        {data.held.map((item) => (
          <li className={dash.minirow} key={`${item.slug}-${item.url}`}>
            <DashLink href={`${ROOT}/following/${encodeURIComponent(item.slug)}#passed`}>
              {item.title}
            </DashLink>
            <span className={`${dash.minirowFigure} ${dash.muted}`}>{item.follow}</span>
          </li>
        ))}
      </ul>
      {data.held_more ? (
        <p className={dash.noticeNext}>
          More are held than the {data.held_cap} listed here; each follow&rsquo;s own page has its
          whole ledger.
        </p>
      ) : null}
    </section>
  );
}

function Table({ rows, data, search }: { rows: FollowListRow[]; data: Following; search: string }) {
  return (
    <>
      <p className={dash.tablecount} role="status">
        <span>
          <span className={dash.shown}>{rows.length}</span> shown
          {data.pagination.has_more ? ", more available" : null}.
        </span>
      </p>

      <div className={dash.tablewrap}>
        <table className={dash.grid}>
          <caption className={dash.srOnly}>
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

      <Pager pagination={data.pagination} search={search} />
    </>
  );
}

function Row({ follow, checksEnabled }: { follow: FollowListRow; checksEnabled: boolean }) {
  return (
    <tr>
      <th scope="row" data-label="Channel">
        <DashLink
          className={dash.rowTitle}
          href={`${ROOT}/following/${encodeURIComponent(follow.slug)}`}
        >
          {follow.title}
        </DashLink>
        <span className={dash.rowMeta}>{follow.kind}</span>
      </th>
      {/* The rule as facts, never as a sentence: sixty rows at 03:00 are a
          column to compare. The sentence the check obeys is one click away. */}
      <td className={styles.colRule} data-label="Rule">
        <RuleFacts follow={follow} />
      </td>
      <td data-label="State">
        <Pill state={follow.state} />
        {/* Printed whether or not the state is `failing`: one rate limit does
            not fail a follow, and a reader looking at a green pill still wants
            to know what the last check hit. */}
        {follow.last_error_code ? <Pill state={follow.last_error_code} tone="bad" /> : null}
      </td>
      <td data-label="Last check">
        <time className={dash.nowrap}>{at(follow.last_check_at)}</time>
      </td>
      <td data-label="Last arrival">
        <time className={dash.nowrap}>{at(follow.last_new_at)}</time>
      </td>
      <td data-label="Next check">
        <span className={dash.nowrap}>{nextCheckWords(follow, checksEnabled)}</span>
      </td>
    </tr>
  );
}

function Pager({ pagination, search }: { pagination: Following["pagination"]; search: string }) {
  const { limit, offset, has_more } = pagination;
  if (!offset && !has_more) return null;
  return (
    <nav className={dash.pager} aria-label="Pagination">
      {offset ? (
        <DashLink
          className={dash.ghostlink}
          href={linkTo(search, { offset: String(Math.max(offset - limit, 0)) })}
        >
          ← Previous
        </DashLink>
      ) : null}
      {has_more ? (
        <DashLink
          className={dash.ghostlink}
          href={linkTo(search, { offset: String(offset + limit) })}
        >
          Next {limit} →
        </DashLink>
      ) : null}
    </nav>
  );
}

/**
 * Follow a channel — `POST /dashboard/following`, which is this page's own
 * path with a different method.
 *
 * When there is nothing to list, this *is* the empty state: one sentence
 * naming what a follow does, then the controls that make one. An empty state
 * you have to leave in order to act on it is a screen that wasted the trip.
 *
 * The Jinja form redirects to the new follow's page. This one stays put and
 * puts the returned row at the top of the table, because the outcome is
 * inline: `already_following` is the tool saying it made nothing, and a
 * redirect cannot say that. The way to the new rule is on the receipt.
 */
function AddForm({
  follows,
  vectors,
  onMade,
}: {
  follows: number;
  vectors: boolean;
  onMade: (row: FollowListRow) => void;
}) {
  const { indexable } = useWriteSide();
  // A ref rather than state: the fields are read by the request this submit
  // makes, not by anything that renders, and a `setState` here would put the
  // POST a render behind the form it came from.
  const fields = useRef<Record<string, string>>({});
  const send = useCallback(() => dashboard.followChannel(fields.current), []);
  const [write, run] = useWrite(send, (outcome) => {
    // `already_following` made nothing, so there is no row to insert — and the
    // one it names is already in the table this page read. What comes back is
    // §21's block, which is the detail row, so it is already the shape this
    // table draws — including the error column, which on a new follow is null
    // because nothing has checked it yet.
    if (outcome.follow && !outcome.already_following) onMade(outcome.follow);
  });

  return (
    <Panel id="add" title="Follow a channel">
      <p className={dash.emptyNote}>
        {follows
          ? "A follow checks a channel or a playlist on its own clock, judges every new upload against a rule you write, and records what it decided either way."
          : "Nothing is followed yet. A follow checks a channel or a playlist on its own clock, indexes the new uploads that match a rule you write, and keeps a ledger of every one it passed over and the number that made the decision."}
      </p>

      {/* §5.5's honest refusal, for the follow form too: `follow_channel`
          raises `E_FEATURE_DISABLED` on the same condition `index_video` does,
          so the page says so above the controls rather than after a
          submission. */}
      {indexable ? null : (
        <p className={styles.panelNote}>
          Indexing is disabled on this instance, so a follow would queue videos it cannot build.
          {vectors ? "" : " Vector search is off as well."} Fix the config or dimension mismatch and
          restart.
        </p>
      )}

      <RuleForm
        onFields={(next) => {
          fields.current = next;
          run();
        }}
      >
        <div className={styles.formrow}>
          <div className={`${dash.field} ${dash.wide}`}>
            <label htmlFor="f-url">Channel or playlist URL</label>
            <input
              id="f-url"
              name="url"
              type="text"
              required
              autoComplete="off"
              spellCheck={false}
              placeholder="https://www.youtube.com/@handle"
              disabled={!indexable}
            />
          </div>
          <div className={`${dash.field} ${dash.wide}`}>
            <label htmlFor="f-title">Name</label>
            <input
              id="f-title"
              name="title"
              type="text"
              autoComplete="off"
              placeholder="read off the URL if you leave it empty"
              disabled={!indexable}
            />
          </div>
        </div>
        <p className={styles.fieldHelp}>
          A channel URL (<code>/@handle</code>, <code>/channel/UC…</code>) or a playlist. A single
          video is not a follow — <DashLink href={`${ROOT}/index`}>Add videos</DashLink> indexes one
          of those.
        </p>

        <RuleFields ns="f" values={ruleValues()} disabled={!indexable} />

        <div className={`${dash.field} ${dash.actions}`}>
          <button
            className={dash.ghostlink}
            type="submit"
            disabled={!indexable || write.status === "sending"}
            // The database's own flag, said where a reader meets it: the rail's
            // foot already prints `indexing refused` for the deployment.
            title={indexable ? undefined : "This instance's database refuses writes."}
          >
            {write.status === "sending" ? "following…" : "Follow"}
          </button>
          <DashLink className={dash.ghostlink} href={`${ROOT}/index`}>
            Add videos
          </DashLink>
        </div>
      </RuleForm>

      {write.status === "done" ? <MadeIt outcome={write.outcome} /> : null}
      {write.status === "failed" ? <Refused error={write.error} /> : null}
    </Panel>
  );
}

/** What the tool did — and `already_following` is the half a redirect could
 *  never say: this URL already had a rule, so nothing was made and the rule in
 *  front of you is the one that was already there. */
function MadeIt({ outcome }: { outcome: FollowCreated }) {
  if (!outcome.follow) {
    return (
      <div className={styles.receipt} role="status">
        <p className={styles.receiptLine}>The follow was accepted.</p>
      </div>
    );
  }
  return (
    <div className={styles.receipt} role="status">
      <p className={styles.receiptLine}>
        <span>{outcome.already_following ? "Already following" : "Now following"}</span>
        <DashLink href={`${ROOT}/following/${encodeURIComponent(outcome.follow.slug)}`}>
          {outcome.follow.title}
        </DashLink>
      </p>
      <p className={styles.receiptNext}>
        {outcome.already_following
          ? "Nothing was made: the tool returns the follow that was already there, unchanged."
          : "Its own page has the rule the check will obey, and everything it passes over."}
      </p>
    </div>
  );
}

/** The refusal, in the API's own words: the code, the message and the `next:`
 *  line are policy text and stay Python's. */
function Refused({ error }: { error: unknown }) {
  const refusal = refusalOf(error);
  return (
    <div className={styles.receipt} role="status">
      <p className={`${styles.receiptLine} ${styles.outcomeBad}`}>
        <code>{refusal.code}</code>
        <span>{refusal.message}</span>
      </p>
      {refusal.next ? <p className={styles.receiptNext}>{refusal.next}</p> : null}
    </div>
  );
}

/** The page's URL, filtered to the parameters the view takes. Values go as
 *  typed: every clamp is Python's, and one corrected here would be a bound the
 *  reader is never told about — `notes` is where a moved one is disclosed. */
export function apiQuery(search: string): URLSearchParams {
  const from = new URLSearchParams(search);
  const query = new URLSearchParams();
  for (const key of PAGE_KEYS) {
    const value = from.get(key);
    if (value !== null && value.trim()) query.set(key, value.trim());
  }
  return query;
}

/** This page's URL with a parameter changed. */
function linkTo(search: string, changes: Record<string, string>): string {
  const next = apiQuery(search);
  for (const [key, value] of Object.entries(changes)) next.set(key, value);
  const query = next.toString();
  return query ? `${ROOT}/following?${query}` : `${ROOT}/following`;
}
