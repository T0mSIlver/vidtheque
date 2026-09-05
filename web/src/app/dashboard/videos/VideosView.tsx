"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, type FormEvent } from "react";
import { Pill } from "@/components/Pill";
import { dashboard, DashboardError, ROOT } from "@/lib/dashboard/client";
import type { Library, LibraryRow } from "@/lib/dashboard/schemas";
import { count, day, duration } from "@/lib/format";
import dash from "../dashboard.module.css";
import { DashLink, Fact, PageHead, ReadFailure, Reading, Sep, Unbroken } from "../parts";
import { useRead } from "../useRead";
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

const INDEX_STATES = ["pending", "indexing", "ready", "failed", "stale"];
const HAS_VALUES = ["any", "transcript", "ocr", "frames", "all"];
const ORDERS = ["recency", "title", "duration", "indexed_at", "relevance"];

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

export function VideosView() {
  const params = useSearchParams();
  const search = params.toString();
  // Keyed on the query string, which is the whole of this page's input: a new
  // URL is a new read, and nothing else re-runs it.
  const read = useCallback(
    (signal: AbortSignal) => dashboard.library(apiQuery(search), signal),
    [search],
  );
  const state = useRead(read);

  const refusal = state.status === "failed" ? state.error : null;
  // `order=relevance` without a `q`, and a date that will not parse. Both are
  // the tool's own typed refusals, and both are a filter the reader can fix —
  // so the band stays on the page with the other seven controls in it, rather
  // than the whole page becoming the refusal.
  const badFilter = refusal instanceof DashboardError && refusal.status === 400;

  return (
    <>
      <PageHead title="Videos">
        <Narrowing search={search} />
      </PageHead>

      {state.status !== "failed" || badFilter ? <Filters search={search} /> : null}

      {state.status === "loading" ? <Reading /> : null}

      {badFilter && refusal instanceof DashboardError ? (
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

      {state.status === "failed" && !badFilter ? (
        <ReadFailure error={state.error} onRetry={state.reload} />
      ) : null}

      {state.status === "ready" ? <Table data={state.data} search={search} /> : null}
    </>
  );
}

/** What is actually narrowing the table, on the title's own baseline.
 *
 *  The URL's values, not the payload's echo: `filters.published_before` is the
 *  start of the day *after* the one asked for, because that is the bound the
 *  query used, and a reader who typed the ninth must read the ninth back. An
 *  open end is `…` rather than a made-up boundary — "published 2025-01-01 …"
 *  says one end is set, where a filled-in second date would be the page
 *  inventing a filter nobody applied.
 *
 *  The order is deliberately not here: every other entry is a *narrowing*, and
 *  an order takes no rows out. The sorted column's own underline says it. */
function Narrowing({ search }: { search: string }) {
  const params = new URLSearchParams(search);
  const value = (key: string) => params.get(key)?.trim() || "";
  const range = (after: string, before: string) =>
    value(after) || value(before) ? `${value(after) || "…"} – ${value(before) || "…"}` : "";

  const facts: [string, string][] = [];
  const state = value("index_state");
  if (state && state !== "all") facts.push(["state", state]);
  const has = value("has");
  if (has && has !== "any") facts.push(["has", has]);
  const published = range("published_after", "published_before");
  if (published) facts.push(["published", published]);
  const indexed = range("indexed_after", "indexed_before");
  if (indexed) facts.push(["indexed", indexed]);

  // Nothing narrowing is an empty strip, and an empty line under a title is a
  // line that says something is missing — so the whole paragraph is absent.
  if (!facts.length) return null;
  return (
    <>
      {facts.map(([label, text], index) => (
        <Unbroken key={label}>
          <Fact label={label} value={text} />
          {index < facts.length - 1 ? <Sep /> : null}
        </Unbroken>
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
 * that arrived. Controlled inputs here would mean a state to keep in step with
 * a URL that is already the state.
 */
function Filters({ search }: { search: string }) {
  const router = useRouter();
  const params = new URLSearchParams(search);
  const value = (key: string, fallback = "") => params.get(key) ?? fallback;

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const next = new URLSearchParams();
    for (const key of FILTERS) {
      const entry = form.get(key);
      // An empty control is not a filter. The Jinja form sent all nine keys
      // whether or not they held anything, which made every link on the page
      // carry `&channel=&tags=&published_after=` for the reader to send on.
      if (typeof entry === "string" && entry.trim()) next.set(key, entry.trim());
    }
    router.push(next.toString() ? `${ROOT}/videos?${next}` : `${ROOT}/videos`);
  }

  return (
    <form className={styles.filters} key={search} onSubmit={submit}>
      <div className={`${styles.field} ${styles.wide}`}>
        <label htmlFor="f-q">Title, channel or description</label>
        <input
          id="f-q"
          name="q"
          type="search"
          defaultValue={value("q")}
          placeholder="attention, tokenizer…"
          spellCheck={false}
          autoComplete="off"
        />
      </div>
      {/* Fixed widths from here down: a text input sized by the platform and a
          select sized by its own longest option both change width when the
          reader changes what is in them, and one control resizing re-flows the
          row. The band's geometry is the viewport's. */}
      <div className={`${styles.field} ${styles.text}`}>
        <label htmlFor="f-channel">Channel</label>
        <input
          id="f-channel"
          name="channel"
          type="text"
          defaultValue={value("channel")}
          autoComplete="off"
        />
      </div>
      <div className={`${styles.field} ${styles.text}`}>
        <label htmlFor="f-tags">Tags</label>
        <input
          id="f-tags"
          name="tags"
          type="text"
          defaultValue={value("tags")}
          placeholder="topic:attention"
          autoComplete="off"
        />
      </div>
      <div className={`${styles.field} ${styles.pickField}`}>
        <label htmlFor="f-state">State</label>
        <span className={styles.pick}>
          <select id="f-state" name="index_state" defaultValue={value("index_state", "all")}>
            <option value="all">all states</option>
            {INDEX_STATES.map((state) => (
              <option key={state} value={state}>
                {state}
              </option>
            ))}
          </select>
        </span>
      </div>
      <div className={`${styles.field} ${styles.pickField}`}>
        <label htmlFor="f-has">Coverage</label>
        <span className={styles.pick}>
          <select id="f-has" name="has" defaultValue={value("has", "any")}>
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
      <fieldset className={`${styles.field} ${styles.rangeField}`}>
        <legend>Published</legend>
        <div className={styles.range}>
          <label className={dash.srOnly} htmlFor="f-pub-after">
            Published on or after
          </label>
          <input
            id="f-pub-after"
            name="published_after"
            type="date"
            defaultValue={value("published_after")}
          />
          <span className={styles.rangeSep} aria-hidden="true">
            –
          </span>
          <label className={dash.srOnly} htmlFor="f-pub-before">
            Published on or before
          </label>
          <input
            id="f-pub-before"
            name="published_before"
            type="date"
            defaultValue={value("published_before")}
          />
        </div>
      </fieldset>
      <fieldset className={`${styles.field} ${styles.rangeField}`}>
        <legend>Indexed</legend>
        <div className={styles.range}>
          <label className={dash.srOnly} htmlFor="f-idx-after">
            Indexed on or after
          </label>
          <input
            id="f-idx-after"
            name="indexed_after"
            type="date"
            defaultValue={value("indexed_after")}
          />
          <span className={styles.rangeSep} aria-hidden="true">
            –
          </span>
          <label className={dash.srOnly} htmlFor="f-idx-before">
            Indexed on or before
          </label>
          <input
            id="f-idx-before"
            name="indexed_before"
            type="date"
            defaultValue={value("indexed_before")}
          />
        </div>
      </fieldset>
      <div className={`${styles.field} ${styles.pickField}`}>
        <label htmlFor="f-order">Order</label>
        <span className={styles.pick}>
          <select id="f-order" name="order" defaultValue={value("order")}>
            {/* Empty rather than a guess: the default is `relevance` with a
                query and `recency` without one, and that is Python's rule to
                apply. The payload's `order` says which answered. */}
            <option value="">default</option>
            {ORDERS.map((entry) => (
              <option key={entry} value={entry}>
                {entry}
              </option>
            ))}
          </select>
        </span>
      </div>
      <div className={`${styles.field} ${styles.narrow}`}>
        <label htmlFor="f-limit">Rows</label>
        <input
          id="f-limit"
          name="limit"
          type="number"
          min={1}
          max={100}
          defaultValue={value("limit")}
          inputMode="numeric"
        />
      </div>
      <div className={`${styles.field} ${styles.actions}`}>
        <button className={styles.ghostlink} type="submit">
          Apply
        </button>
        <DashLink className={styles.ghostlink} href={`${ROOT}/videos`}>
          Reset
        </DashLink>
      </div>
    </form>
  );
}

function Table({ data, search }: { data: Library; search: string }) {
  const [sortedCol, sortedDir] = SORTED[data.order] ?? [null, null];
  const rows = data.videos;

  return (
    <>
      {/* Where a clamp is disclosed. The Jinja page echoed an accepted `limit`
          back into the field the reader typed it into; a payload has no form,
          so the sentence rides on `notes` — policy text, rendered here and
          composed in Python. */}
      {data.notes.length ? (
        <ul className={styles.notes}>
          {data.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      ) : null}

      {rows.length ? (
        <>
          <p className={styles.tablecount} role="status">
            <span>
              <span className={styles.shown}>{rows.length}</span> shown of{" "}
              <span className={styles.shown}>{count(data.total)}</span>
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
                    search={search}
                    sort={sortedCol === "title" ? sortedDir : null}
                  />
                  <SortHead
                    label="Published"
                    order="recency"
                    search={search}
                    sort={sortedCol === "published" ? sortedDir : null}
                  />
                  <SortHead
                    label="Duration"
                    order="duration"
                    search={search}
                    num
                    sort={sortedCol === "duration" ? sortedDir : null}
                  />
                  <th scope="col">State</th>
                  <th scope="col">Coverage</th>
                  <th scope="col">Tags</th>
                  <SortHead
                    label="Indexed"
                    order="indexed_at"
                    search={search}
                    sort={sortedCol === "indexed" ? sortedDir : null}
                  />
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <Row key={row.video_id} row={row} />
                ))}
              </tbody>
            </table>
          </div>

          <Pager pagination={data.pagination} search={search} />
        </>
      ) : (
        <Empty data={data} search={search} />
      )}
    </>
  );
}

function SortHead({
  label,
  order,
  search,
  sort,
  num,
}: {
  label: string;
  order: string;
  search: string;
  sort: "ascending" | "descending" | null;
  num?: boolean;
}) {
  // A new order is a new set in a new arrangement, so the pager goes back to
  // the top of it.
  const href = linkTo(search, { order, offset: null });
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

function Row({ row }: { row: LibraryRow }) {
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
        <DashLink className={styles.rowTitle} href={href}>
          {row.title}
        </DashLink>
        <span className={styles.rowMeta}>
          {row.channel}
          <Sep />
          <code>{row.video_id}</code>
        </span>
      </th>
      <td data-label="Published">
        <time className={styles.nowrap}>{day(row.published_at)}</time>
      </td>
      <td className={dash.num} data-label="Duration">
        {duration(row.duration_s)}
      </td>
      <td data-label="State">
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
          <span className={styles.muted}>—</span>
        )}
      </td>
      <td data-label="Indexed">
        <time className={styles.nowrap}>{day(row.indexed_at)}</time>
      </td>
    </tr>
  );
}

function Pager({ pagination, search }: { pagination: Library["pagination"]; search: string }) {
  const { limit, offset, has_more } = pagination;
  if (!offset && !has_more) return null;
  return (
    <nav className={styles.pager} aria-label="Pagination">
      {offset ? (
        <DashLink
          className={styles.ghostlink}
          href={linkTo(search, { offset: String(Math.max(offset - limit, 0)) })}
        >
          ← Previous
        </DashLink>
      ) : null}
      {has_more ? (
        <DashLink
          className={styles.ghostlink}
          href={linkTo(search, { offset: String(offset + limit) })}
        >
          Next {limit} →
        </DashLink>
      ) : null}
    </nav>
  );
}

/** No rows, in the two ways that happens. */
function Empty({ data, search }: { data: Library; search: string }) {
  const params = new URLSearchParams(search);
  const set = (key: string) => Boolean(params.get(key)?.trim());
  const dated =
    set("published_after") ||
    set("published_before") ||
    set("indexed_after") ||
    set("indexed_before");
  const state = params.get("index_state")?.trim();
  const has = params.get("has")?.trim();

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
          <DashLink href={linkTo(search, { offset: String(data.pagination.last_offset) })}>
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
            The {set("published_after") || set("published_before") ? "published" : "indexed"} date
            range is narrowing it.
          </>
        ) : state && state !== "all" ? (
          <>
            The state filter is on <code>{state}</code>.
          </>
        ) : has && has !== "any" ? (
          <>
            The coverage filter is <code>has={has}</code>.
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

/** The page's URL, filtered down to the parameters the contract takes.
 *
 *  A whitelist rather than a passthrough: this is the string that becomes a
 *  request, and an unknown key in the URL bar has no business reaching the API
 *  just because somebody pasted it. Values are sent exactly as typed — the
 *  clamps are Python's, and a value corrected here would be a clamp the reader
 *  is never told about. */
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
  return query ? `${ROOT}/videos?${query}` : `${ROOT}/videos`;
}
