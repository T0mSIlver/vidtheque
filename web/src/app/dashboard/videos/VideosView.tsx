"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback } from "react";
import { Pill } from "@/components/Pill";
import { dashboard, DashboardError, echoOf, ROOT } from "@/lib/dashboard/client";
import {
  type Library,
  type LibraryFilters,
  type LibraryRow,
  RefusedLibrary,
} from "@/lib/dashboard/schemas";
import { count, day, duration } from "@/lib/format";
import { useFilterBand } from "../band";
import dash from "../dashboard.module.css";
import {
  DashLink,
  Fact,
  PageHead,
  ReadFailure,
  Reading,
  Sep,
  Unbroken,
  useWriteSide,
} from "../parts";
import { useRead } from "../useRead";
import { ReindexControl } from "./Manage";
import styles from "./videos.module.css";

// The videos table — `templates/videos.html`, reading `GET /dashboard/api/library`
// in the browser (dashboard.md §5.2, §20).
//
// **The filters are the URL**, exactly as they were in Jinja, and for the same
// reason: a filtered table is a link somebody can send, the back button walks
// the searches you actually ran, and there is one copy of the query rather than
// a component state that has to be kept in step with it. The band submits by
// navigation — never a fetch per keystroke, which would put a request on the
// wire for every letter of a channel name and make the visible rows a race.
//
// Every bound is Python's. This page sends what the reader typed and renders
// what came back: `notes` is where a moved clamp is disclosed (`limit=100000 →
// 100`), `order` is echoed because it was never inferrable from `q`, and
// `total` is the exact count of the filtered set rather than the tool's `~`
// probe — a tilde over a table with a Next button is the one thing on the line
// a reader cannot act on.
//
// **Every control and every link reads the server's resolved filters, not the
// URL.** `?has=bogus` ran as `has=any`, `?limit=100000` ran as a hundred, and a
// date bound is clamped and snapped to a whole UTC day before it filters — so a
// band seeded from the raw query string would name filters that never ran, next
// to a `note:` saying which ones did. The payload's `filters` echo is the
// template's `filters` dict, and this page uses it in all four places Jinja did:
// the narrowing strip, the band, the links, and the empty state's one line.

/** Every parameter the contract lists, in the order the band asks them. */
const FILTERS = [
  "q",
  "channel",
  "tags",
  "index_state",
  "has",
  "published_after",
  "published_before",
  "indexed_after",
  "indexed_before",
  "order",
  "limit",
] as const;

// `offset` is the pager's, not the band's: changing a filter changes the set,
// and page four of the old set is not page four of the new one.
const PAGE_KEYS = [...FILTERS, "offset"];

/** The nine keys `videos.html`'s `carried()` macro put on every link, in its
 *  order.
 *
 *  All nine, empty or not. Every link on this page is the current query with
 *  one thing changed — a page, or an order — and a date range that survives the
 *  pager but not the sort head is a filter the reader has to re-apply. A key
 *  that disappears when its box is empty also makes two URLs for one query,
 *  which is the state of "no filters" spelled two ways. */
const CARRIED = [
  "q",
  "channel",
  "tags",
  "has",
  "index_state",
  "published_after",
  "published_before",
  "indexed_after",
  "indexed_before",
] as const;

const INDEX_STATES = ["pending", "indexing", "ready", "failed", "stale"];
const HAS_VALUES = ["any", "transcript", "ocr", "frames", "all"];
const ORDERS = ["recency", "title", "duration", "indexed_at", "relevance"];

/** The four date controls, which are the ones the server resolves before it
 *  filters — so they are read back off the payload rather than out of the URL. */
const DATE_KEYS = [
  "published_after",
  "published_before",
  "indexed_after",
  "indexed_before",
] as const;
type DateKey = (typeof DATE_KEYS)[number];

const DAY_S = 86_400;

// A picker sitting on the value the API would have used anyway. Sending it is
// not wrong, but it puts `&index_state=all&has=any` on every link a reader
// copies out of the address bar to say nothing at all — and it makes two URLs
// for one query. It is the one thing `carried()` printed that this page drops.
const DEFAULTS: Record<string, string> = { index_state: "all", has: "any" };

// Which column head wears the accent underline, and which way that order runs
// (`_LIST_ORDER`): titles A→Z, everything else newest or longest first. There
// is no opposite variant to toggle to, so a head is a statement, not a switch.
const SORTED: Record<string, [string, "ascending" | "descending"]> = {
  title: ["title", "ascending"],
  recency: ["published", "descending"],
  duration: ["duration", "descending"],
  indexed_at: ["indexed", "descending"],
};

// The `t/o/f` letters, with the words behind them. The payload sends the three
// booleans; the letters and their labels are this page's rendering of them.
const COVERAGE: [keyof LibraryRow["coverage"], string, string][] = [
  ["transcript", "t", "transcript"],
  ["ocr", "o", "on-screen text"],
  ["frames", "f", "frame embeddings"],
];

/** The template's `filters` dict: every control's value and every link's, after
 *  the server has had its say. */
type Band = Record<(typeof FILTERS)[number], string>;

export function VideosView() {
  const params = useSearchParams();
  const search = params.toString();
  // Keyed on the query string, which is the whole of this page's input: a new
  // URL is a new read, and nothing else re-runs it. The answer carries the URL
  // it answered, because a read in flight leaves the *previous* page's payload
  // in hand — and a band seeded from that would show the last query's page size
  // beside this query's rows.
  const read = useCallback(
    async (signal: AbortSignal) => ({
      search,
      data: await dashboard.library(apiQuery(search), signal),
    }),
    [search],
  );
  const state = useRead(read);
  const answered =
    state.status === "ready" && state.data.search === search ? state.data.data : undefined;

  const refusal = state.status === "failed" ? state.error : null;
  // A refused read still says what it resolved (§20): the same `filters` block,
  // from the same function, beside the envelope. Without it the date pickers
  // and the line under the title can only redraw what was typed — which is the
  // one reading the server has just said is not what it ran.
  const resolved = echoOf(refusal, RefusedLibrary)?.filters;
  const band = bandOf(search, answered, resolved);
  // The gate's two refusals are the shell's, and they replaced the whole page in
  // Jinja too — the 401 before any view ran (`views.py:166-185`) and the 429 in
  // the limiter ahead of it. There is no filter to fix behind either, and a band
  // over one is an invitation to a page this browser cannot read.
  const gated =
    refusal instanceof DashboardError && (refusal.status === 401 || refusal.status === 429);
  // Everything else is the view's own refusal, and `views.py:617-665` rendered
  // the band with it whatever the status: a refused date must still leave the
  // reader the other seven controls to fix it with, and a 500 takes the same
  // page a 400 does.
  const told = refusal instanceof DashboardError && !gated;

  return (
    <>
      <PageHead title="Videos">
        <Narrowing band={band} />
      </PageHead>

      {gated ? null : (
        // "Settled" is the payload having answered *this* URL, not the read
        // having stopped: a filter change leaves the previous answer in hand,
        // so the band is handed two nodes — the reader's URL, then the server's
        // reply to it — and the caret is owed to both.
        <Filters
          band={band}
          search={search}
          settled={answered !== undefined || state.status === "failed"}
        />
      )}

      {state.status === "loading" ? <Reading /> : null}

      {told && refusal instanceof DashboardError ? (
        <section className={dash.notice} aria-labelledby="filter-refused">
          <h2 className={dash.noticeTitle} id="filter-refused">
            {refusal.message}
          </h2>
          <p className={dash.noticeDetail}>
            <code>{refusal.code}</code>
          </p>
          {refusal.next ? <p className={dash.noticeNext}>{refusal.next}</p> : null}
        </section>
      ) : null}

      {state.status === "failed" && !told ? (
        <ReadFailure error={state.error} onRetry={state.reload} />
      ) : null}

      {state.status === "ready" ? (
        <Table band={band} data={state.data.data} offset={params.get("offset") ?? ""} />
      ) : null}
    </>
  );
}

/** What is actually narrowing the table, on the title's own baseline.
 *
 *  The values are the ones the query ran with, not the strings the URL happened
 *  to carry: `?has=bogus` ran as `any` and prints nothing, and a date the server
 *  clamped or snapped prints the day it snapped to. A line re-printing the raw
 *  URL would be the page vouching for a filter that never ran. An open end is
 *  `…` rather than a made-up boundary — "published 2025-01-01 …" says one end is
 *  set, where a filled-in second date would be the page inventing a filter
 *  nobody applied.
 *
 *  The order is deliberately not here: every other entry is a *narrowing*, and
 *  an order takes no rows out. The sorted column's own underline says it. */
function Narrowing({ band }: { band: Band }) {
  const range = (after: DateKey, before: DateKey) =>
    band[after] || band[before] ? `${band[after] || "…"} – ${band[before] || "…"}` : "";

  const facts: [string, string][] = [];
  if (band.index_state && band.index_state !== "all") facts.push(["state", band.index_state]);
  if (band.has && band.has !== "any") facts.push(["has", band.has]);
  const published = range("published_after", "published_before");
  if (published) facts.push(["published", published]);
  const indexed = range("indexed_after", "indexed_before");
  if (indexed) facts.push(["indexed", indexed]);

  // Nothing narrowing is an empty strip, and an empty line under a title is a
  // line that says something is missing — so the whole paragraph is absent.
  if (!facts.length) return null;
  return (
    <>
      {/* The separator belongs to the fact before it, and the space after it
          is written out: JSX drops whitespace that holds a newline, so without
          it the strip has no break opportunity and runs off a narrow screen. */}
      {facts.map(([label, text], index) => (
        <span key={label}>
          <Unbroken>
            <Fact label={label} value={text} />
            {index < facts.length - 1 ? <Sep /> : null}
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
 * **A change to any control *is* the search** (`dashboard.js`'s `data-autosubmit`
 * appender, here as `useFilterBand`): a picker submits on the spot, a text field
 * submits when the typing pauses, and the caret comes back into the box that
 * caused the navigation. More requests, and they are cheap against a local
 * SQLite index; what they buy is a band you use rather than a form you fill in
 * and then have to remember to submit. `Apply` stays in the markup — it is what
 * a browser that never ran the script uses — and is hidden only once the script
 * has taken the job over. Reset is a link and not a submit, so it keeps its job.
 *
 * Seeded with `defaultValue`, so the browser owns what is being typed;
 * controlled inputs here would mean a state to keep in step with a URL that is
 * already the state. The band is re-keyed on everything it is seeded from —
 * the query string, which is the reader's half, and the server's resolved
 * filters, which arrive one read later — because re-keying is how an
 * uncontrolled input is re-seeded when the answer lands.
 */
function Filters({ band, search, settled }: { band: Band; search: string; settled: boolean }) {
  const router = useRouter();

  const submit = useCallback(
    (form: HTMLFormElement) => {
      const entries = new FormData(form);
      const next = new URLSearchParams();
      const chosen = (key: string) => {
        const entry = entries.get(key);
        return typeof entry === "string" ? entry.trim() : "";
      };
      // The nine keys, whatever they hold — `carried()`'s own list — minus a
      // picker resting on the value the API would have used anyway.
      for (const key of CARRIED) {
        const value = chosen(key);
        if (value !== DEFAULTS[key]) next.set(key, value);
      }
      // The two that are not narrowings but are still the reader's: they are
      // controls in this band, so a submit carries what they are set to.
      for (const key of ["order", "limit"]) {
        const value = chosen(key);
        if (value) next.set(key, value);
      }
      // `offset` is not carried: a new filter is a new set, and page four of
      // the old one is not page four of the new one.
      router.push(next.toString() ? `${ROOT}/videos?${next}` : `${ROOT}/videos`);
    },
    [router],
  );

  const { attach, scripted, onSubmit } = useFilterBand(submit, settled);
  // Everything the controls are seeded from, in one string. The dates keep
  // their own keys as well: the two ends of a range are siblings, and a key
  // that is only the value is one key for both of them when both are empty.
  //
  // The separator is a NUL, written as an escape: a literal one in the source
  // makes every tool that sniffs for binary — `grep` first — stop reading this
  // file. It is the one character no filter value can contain.
  const seed = `${search}|${FILTERS.map((key) => band[key]).join("\u0000")}`;

  return (
    <form className={dash.filters} key={seed} onSubmit={onSubmit} ref={attach}>
      <div className={`${dash.field} ${dash.wide}`}>
        <label htmlFor="f-q">Title, channel or description</label>
        <input
          id="f-q"
          name="q"
          type="search"
          defaultValue={band.q}
          placeholder="attention, tokenizer…"
          spellCheck={false}
          autoComplete="off"
        />
      </div>
      {/* Fixed widths from here down: a text input sized by the platform and a
          select sized by its own longest option both change width when the
          reader changes what is in them, and one control resizing re-flows the
          row. The band's geometry is the viewport's. */}
      <div className={`${dash.field} ${dash.text}`}>
        <label htmlFor="f-channel">Channel</label>
        <input
          id="f-channel"
          name="channel"
          type="text"
          defaultValue={band.channel}
          autoComplete="off"
        />
      </div>
      <div className={`${dash.field} ${dash.text}`}>
        <label htmlFor="f-tags">Tags</label>
        <input
          id="f-tags"
          name="tags"
          type="text"
          defaultValue={band.tags}
          placeholder="topic:attention"
          autoComplete="off"
        />
      </div>
      <div className={`${dash.field} ${dash.pickField}`}>
        <label htmlFor="f-state">State</label>
        <span className={dash.pick}>
          <select id="f-state" name="index_state" defaultValue={band.index_state}>
            <option value="all">all states</option>
            {INDEX_STATES.map((state) => (
              <option key={state} value={state}>
                {state}
              </option>
            ))}
          </select>
        </span>
      </div>
      <div className={`${dash.field} ${dash.pickField}`}>
        <label htmlFor="f-has">Coverage</label>
        <span className={dash.pick}>
          <select id="f-has" name="has" defaultValue={band.has}>
            {HAS_VALUES.map((entry) => (
              <option key={entry} value={entry}>
                {entry}
              </option>
            ))}
          </select>
        </span>
      </div>
      {/* The two time axes, as two controls, never one (the CLAUDE.md
          invariant): `published` picks which videos, `indexed` picks when this
          box did the work. A fieldset because a range is one question asked
          with two inputs, and a screen reader needs the legend to know the
          second date belongs to the first. */}
      <fieldset className={`${dash.field} ${styles.rangeField}`}>
        <legend>Published</legend>
        <div className={styles.range}>
          <label className={dash.srOnly} htmlFor="f-pub-after">
            Published on or after
          </label>
          <input
            key={`published_after:${band.published_after}`}
            id="f-pub-after"
            name="published_after"
            type="date"
            defaultValue={band.published_after}
          />
          <span className={styles.rangeSep} aria-hidden="true">
            –
          </span>
          <label className={dash.srOnly} htmlFor="f-pub-before">
            Published on or before
          </label>
          <input
            key={`published_before:${band.published_before}`}
            id="f-pub-before"
            name="published_before"
            type="date"
            defaultValue={band.published_before}
          />
        </div>
      </fieldset>
      <fieldset className={`${dash.field} ${styles.rangeField}`}>
        <legend>Indexed</legend>
        <div className={styles.range}>
          <label className={dash.srOnly} htmlFor="f-idx-after">
            Indexed on or after
          </label>
          <input
            key={`indexed_after:${band.indexed_after}`}
            id="f-idx-after"
            name="indexed_after"
            type="date"
            defaultValue={band.indexed_after}
          />
          <span className={styles.rangeSep} aria-hidden="true">
            –
          </span>
          <label className={dash.srOnly} htmlFor="f-idx-before">
            Indexed on or before
          </label>
          <input
            key={`indexed_before:${band.indexed_before}`}
            id="f-idx-before"
            name="indexed_before"
            type="date"
            defaultValue={band.indexed_before}
          />
        </div>
      </fieldset>
      <div className={`${dash.field} ${dash.pickField}`}>
        <label htmlFor="f-order">Order</label>
        <span className={dash.pick}>
          {/* The five the tool takes, and no sixth. An "unset" option would be
              the one entry on this picker that does not name an order, on a
              page whose whole job is to say which query ran — the default is
              `relevance` with a query and `recency` without one, Python decides
              which, and the payload's `order` is what this sits on. */}
          <select id="f-order" name="order" defaultValue={band.order}>
            {ORDERS.map((entry) => (
              <option key={entry} value={entry}>
                {entry}
              </option>
            ))}
          </select>
        </span>
      </div>
      <div className={`${dash.field} ${dash.narrow}`}>
        <label htmlFor="f-limit">Rows</label>
        {/* No `max`: the ceiling is `OWNER_CLAMPS.videos_max_limit` and this
            page has no copy of it. A hundred hardcoded here would be a second
            bound, wrong on the deployment that moved its own — and the real
            one already answers, with the rows it clamped to and the `note:`
            that names both numbers. The box holds the limit that was
            *accepted*, which is how a reader who asked for 100000 sees what
            they got. */}
        <input
          id="f-limit"
          name="limit"
          type="number"
          min={1}
          defaultValue={band.limit}
          inputMode="numeric"
        />
      </div>
      <div className={`${dash.field} ${dash.actions}`}>
        {/* Hidden, not removed: it is the control a browser that never ran the
            band's script submits with, and `hidden` is what the appender set. */}
        <button className={dash.ghostlink} type="submit" hidden={scripted}>
          Apply
        </button>
        <DashLink className={dash.ghostlink} href={`${ROOT}/videos`}>
          Reset
        </DashLink>
      </div>
    </form>
  );
}

function Table({ band, data, offset }: { band: Band; data: Library; offset: string }) {
  const [sortedCol, sortedDir] = SORTED[data.order] ?? [null, null];
  const rows = data.videos;
  const carried = carriedOf(band, offset);
  // Present in a private deployment, absent in the demo projection — §2.4's
  // table, decided by the same list of routes that decides everything else on
  // the write side. Not a disabled column: a control that cannot work is worse
  // UI than no column.
  const { rendered } = useWriteSide();

  return (
    <>
      {/* Where a clamp is disclosed. The Jinja page echoed an accepted `limit`
          back into the field the reader typed it into; a payload has no form,
          so the sentence rides on `notes` — policy text, rendered here and
          composed in Python. */}
      {data.notes.length ? (
        <ul className={dash.notes}>
          {data.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      ) : null}

      {rows.length ? (
        <>
          <p className={dash.tablecount} role="status">
            <span>
              <span className={dash.shown}>{rows.length}</span> shown of{" "}
              <span className={dash.shown}>{count(data.total)}</span>
              {data.pagination.has_more ? ", more available" : null}.
            </span>
          </p>

          <div className={dash.tablewrap}>
            <table className={`${dash.grid} ${styles.videos}`}>
              <caption className={dash.srOnly}>
                Videos in the corpus, with the state of each pipeline leg
              </caption>
              <thead>
                <tr>
                  <th scope="col" className={styles.colShot}>
                    <span className={dash.srOnly}>Frame</span>
                  </th>
                  <SortHead
                    label="Title"
                    order="title"
                    carried={carried}
                    sort={sortedCol === "title" ? sortedDir : null}
                  />
                  <SortHead
                    label="Published"
                    order="recency"
                    carried={carried}
                    sort={sortedCol === "published" ? sortedDir : null}
                  />
                  <SortHead
                    label="Duration"
                    order="duration"
                    carried={carried}
                    num
                    sort={sortedCol === "duration" ? sortedDir : null}
                  />
                  <th scope="col">State</th>
                  <th scope="col">Coverage</th>
                  <th scope="col">Tags</th>
                  <SortHead
                    label="Indexed"
                    order="indexed_at"
                    carried={carried}
                    sort={sortedCol === "indexed" ? sortedDir : null}
                  />
                  {rendered ? (
                    <th scope="col" className={styles.colActions}>
                      Actions
                    </th>
                  ) : null}
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <Row key={row.video_id} row={row} actions={rendered} />
                ))}
              </tbody>
            </table>
          </div>

          <Pager pagination={data.pagination} carried={carried} />
        </>
      ) : (
        <Empty band={band} carried={carried} data={data} />
      )}
    </>
  );
}

function SortHead({
  label,
  order,
  carried,
  sort,
  num,
}: {
  label: string;
  order: string;
  carried: URLSearchParams;
  sort: "ascending" | "descending" | null;
  num?: boolean;
}) {
  // A new order is a new set in a new arrangement, so the pager goes back to
  // the top of it — `sort_link` carried the filters and the page size, never
  // the offset.
  const href = linkTo(carried, { order, offset: null });
  return (
    <th
      scope="col"
      className={num ? dash.num : undefined}
      aria-sort={sort ?? undefined}
      // The sorted head's own underline is redrawn in the accent by the
      // stylesheet — no caret glyph, because this surface has no icon language
      // and a sort arrow is not where one should start.
    >
      <DashLink href={href}>{label}</DashLink>
    </th>
  );
}

function Row({ row, actions }: { row: LibraryRow; actions: boolean }) {
  const href = `${ROOT}/videos/${encodeURIComponent(row.video_id)}`;
  return (
    <tr>
      <td className={styles.colShot} data-label="Frame">
        {row.thumb ? (
          // A `/frames/…` URL on Python's origin, signed and expiring on the
          // owner's instance and already sized by the API at the width it is
          // displayed at. The optimizer would fetch and cache it past its own
          // signature, for a 96px still.
          // eslint-disable-next-line @next/next/no-img-element
          <img
            className={dash.thumb}
            src={row.thumb}
            alt=""
            width={96}
            height={54}
            loading="lazy"
            decoding="async"
          />
        ) : (
          <span className={`${dash.thumb} ${dash.thumbEmpty}`}>no frame</span>
        )}
      </td>
      <th scope="row" className={styles.colTitle} data-label="Title">
        <DashLink className={dash.rowTitle} href={href}>
          {row.title}
        </DashLink>
        <span className={dash.rowMeta}>
          {row.channel}
          <Sep /> <code>{row.video_id}</code>
        </span>
      </th>
      <td data-label="Published">
        <time className={dash.nowrap}>{day(row.published_at)}</time>
      </td>
      <td className={dash.num} data-label="Duration">
        {duration(row.duration_s)}
      </td>
      <td className={styles.colState} data-label="State">
        <Pill state={row.index_state} />
      </td>
      <td data-label="Coverage">
        <span className={styles.coverage}>
          {COVERAGE.map(([key, letter, label]) => {
            const present = row.coverage[key];
            return (
              <span
                key={key}
                className={`${styles.cov} ${present ? styles.covOn : styles.covOff}`}
                title={present ? label : `${label} — missing`}
              >
                <span aria-hidden="true">{letter}</span>
                <span
                  className={dash.srOnly}
                >{`${label}: ${present ? "present" : "missing"}`}</span>
              </span>
            );
          })}
        </span>
      </td>
      <td data-label="Tags">
        {row.tags.length ? (
          <span className={styles.taglist}>
            {row.tags.map((tag) => (
              <DashLink
                key={tag}
                className={`${dash.chip} ${styles.chipSmall}`}
                href={`${ROOT}/videos?tags=${encodeURIComponent(tag)}&index_state=all`}
              >
                {tag}
              </DashLink>
            ))}
          </span>
        ) : (
          <span className={dash.muted}>—</span>
        )}
      </td>
      <td data-label="Indexed">
        <time className={dash.nowrap}>{day(row.indexed_at)}</time>
      </td>
      {/* One control and one link. Re-index is a single decision and fits a
          34px row; tagging needs two text fields and lives on the detail page,
          where there is room for the namespace rules beside them. */}
      {actions ? (
        <td className={styles.colActions} data-label="Actions">
          <ReindexControl videoId={row.video_id} label="Re-index" />
          <DashLink className={dash.ghostlink} href={`${href}#manage`}>
            Tag
          </DashLink>
        </td>
      ) : null}
    </tr>
  );
}

function Pager({
  pagination,
  carried,
}: {
  pagination: Library["pagination"];
  carried: URLSearchParams;
}) {
  const { limit, offset, has_more } = pagination;
  if (!offset && !has_more) return null;
  return (
    <nav className={dash.pager} aria-label="Pagination">
      {offset ? (
        <DashLink
          className={dash.ghostlink}
          href={linkTo(carried, { offset: String(Math.max(offset - limit, 0)) })}
        >
          ← Previous
        </DashLink>
      ) : null}
      {has_more ? (
        <DashLink
          className={dash.ghostlink}
          href={linkTo(carried, { offset: String(offset + limit) })}
        >
          Next {limit} →
        </DashLink>
      ) : null}
    </nav>
  );
}

/** No rows, in the two ways that happens. */
function Empty({ band, carried, data }: { band: Band; carried: URLSearchParams; data: Library }) {
  const dated =
    band.published_after || band.published_before || band.indexed_after || band.indexed_before;

  // Paged off the end rather than filtered to nothing. `last_offset` arrives
  // exactly then, and it is where the last page starts — so the way back is a
  // link and not a subtraction the reader has to do.
  if (data.pagination.last_offset !== undefined) {
    return (
      <section className={dash.notice} aria-labelledby="past-end">
        <h2 className={dash.noticeTitle} id="past-end">
          That page is past the end of {count(data.total)} matching video(s).
        </h2>
        <p className={dash.noticeNext}>
          <DashLink href={linkTo(carried, { offset: String(data.pagination.last_offset) })}>
            Go to the last page
          </DashLink>
        </p>
      </section>
    );
  }

  return (
    <section className={dash.notice} aria-labelledby="no-match">
      <h2 className={dash.noticeTitle} id="no-match">
        Nothing matches those filters.
      </h2>
      {/* One line, naming the filter doing the narrowing — the date range
          first when it is set, because its two ends live in a fieldset and
          neither is a word in the query box. */}
      <p className={dash.noticeDetail}>
        {dated ? (
          <>
            The {band.published_after || band.published_before ? "published" : "indexed"} date range
            is narrowing it.
          </>
        ) : band.index_state !== "all" ? (
          <>
            The state filter is on <code>{band.index_state}</code>.
          </>
        ) : band.has !== "any" ? (
          <>
            The coverage filter is <code>has={band.has}</code>.
          </>
        ) : (
          <>The text, channel and tag boxes are what narrowed it.</>
        )}
      </p>
      <p className={dash.noticeNext}>
        <DashLink href={`${ROOT}/videos`}>Show everything</DashLink>
      </p>
    </section>
  );
}

/** The template's `filters` dict: what the query actually ran with, with the
 *  reader's own URL standing in until the answer says otherwise.
 *
 *  The dates are the four the server resolves before it filters (§20): `30d`
 *  and a bare unix stamp are good things to be able to type into a URL and are
 *  not days, a bound outside [1970, a year out] is pulled back to the edge, and
 *  every accepted value is snapped to its UTC day. `_before` is *exclusive* —
 *  the start of the day after the one asked for, which is what makes
 *  `published_before=2026-08-09` include the ninth — so it is read back a day
 *  earlier and the reader sees the date they asked for. The floor is a second
 *  rather than midnight, because `day` prints the dash for a falsy stamp and a
 *  date box cannot be seeded from a dash.
 *
 *  `tags` has no echo to read: the payload carries the parsed list and the box
 *  holds the string it was parsed from, so that one stays the URL's. */
function bandOf(search: string, data?: Library, resolved?: LibraryFilters): Band {
  const params = new URLSearchParams(search);
  const raw = (key: string, fallback = "") => params.get(key)?.trim() || fallback;
  // The answer's block, or — on a refusal — the one the refusal echoed. `order`
  // and `limit` stay the URL's there: they are not in that block, and a refused
  // read resolved no page size.
  const filters = data?.filters ?? resolved;

  const dates = {} as Record<DateKey, string>;
  for (const key of DATE_KEYS) {
    if (!filters) {
      dates[key] = raw(key);
      continue;
    }
    const echoed = filters[key];
    const asked = echoed === null ? null : key.endsWith("_before") ? echoed - DAY_S : echoed;
    dates[key] = asked === null ? "" : day(Math.max(asked, 1));
  }

  return {
    q: filters ? (filters.q ?? "") : raw("q"),
    channel: filters ? (filters.channel ?? "") : raw("channel"),
    tags: raw("tags"),
    index_state: filters ? filters.index_state : raw("index_state", "all"),
    has: filters ? filters.has : raw("has", "any"),
    ...dates,
    order: data ? data.order : raw("order"),
    limit: data ? String(data.pagination.limit) : raw("limit"),
  };
}

/** The parts of every link on this page that do not change — `carried()`, plus
 *  the two the sort and page macros appended and the offset the pager moves. */
function carriedOf(band: Band, offset: string): URLSearchParams {
  const params = new URLSearchParams();
  for (const key of CARRIED) {
    if (band[key] === DEFAULTS[key]) continue;
    params.set(key, band[key]);
  }
  if (band.order) params.set("order", band.order);
  if (band.limit) params.set("limit", band.limit);
  if (offset.trim()) params.set("offset", offset.trim());
  return params;
}

/** The page's URL, filtered down to the parameters the contract takes.
 *
 *  A whitelist rather than a passthrough: this is the string that becomes a
 *  request, and an unknown key in the URL bar has no business reaching the API
 *  just because somebody pasted it. Values are sent exactly as typed — the
 *  clamps are Python's, and a value corrected here would be a clamp the reader
 *  is never told about. An empty one is not sent at all: the links carry the
 *  key so a reader can see the whole shape of the query, and the API is asked
 *  only what is being asked of it. */
export function apiQuery(search: string): URLSearchParams {
  const from = new URLSearchParams(search);
  const query = new URLSearchParams();
  for (const key of PAGE_KEYS) {
    const value = from.get(key);
    if (value !== null && value.trim()) query.set(key, value.trim());
  }
  return query;
}

/** This page's carried query with some of its parameters changed; `null`
 *  removes one. */
function linkTo(carried: URLSearchParams, changes: Record<string, string | null>): string {
  const next = new URLSearchParams(carried);
  for (const [key, value] of Object.entries(changes)) {
    if (value === null) next.delete(key);
    else next.set(key, value);
  }
  const query = next.toString();
  return query ? `${ROOT}/videos?${query}` : `${ROOT}/videos`;
}
