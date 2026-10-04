"use client";

import { useSearchParams } from "next/navigation";
import { Pill } from "@/components/ui/Pill";
import { dashboard, DashboardError, echoOf, ROOT } from "@/lib/dashboard/client";
import { useResource } from "@/lib/dashboard/resource";
import { type Library, type LibraryRow, RefusedLibrary } from "@/lib/dashboard/schemas";
import { count, day, hms } from "@/lib/format";
import { Notice, ReadFailure, RefusalNotice } from "@/components/dashboard/kit/notice";
import { Notes, Pager, table, TableCount } from "@/components/dashboard/kit/table";
import { Body, DashLink, Page, PageHead, Sep, ui } from "@/components/dashboard/kit/ui";
import { Filters } from "./Filters";
import { apiQuery, type Band, bandOf, carriedOf, linkTo } from "./query";
import styles from "./videos.module.css";

// The videos table (dashboard.md §5.2, §20). The filters are the URL, every
// bound is Python's, and every control and link reads the filters the query
// ran with rather than the URL's words.

// Which head wears the accent, and which way that order runs (`_LIST_ORDER`).
const SORTED: Record<string, [string, "ascending" | "descending"]> = {
  title: ["title", "ascending"],
  recency: ["published", "descending"],
  duration: ["duration", "descending"],
  indexed_at: ["indexed", "descending"],
};

// Each leg's word when it is missing, and its sentence behind it.
const COVERAGE: [keyof LibraryRow["coverage"], string, string][] = [
  ["transcript", "transcript", "transcript"],
  ["ocr", "ocr", "on-screen text"],
  ["frames", "frames", "frame embeddings"],
];

export function VideosView() {
  const params = useSearchParams();
  const query = apiQuery(params);
  const library = useResource(`library?${query}`, (signal) => dashboard.library(query, signal));
  const { data, error } = library;

  // A refused read still echoes what it resolved (§20), so the band shows it.
  const band = bandOf(params, data, echoOf(error, RefusedLibrary)?.filters);
  // The gate's refusals replace the page: there is no filter to fix behind them.
  const gated = error instanceof DashboardError && (error.status === 401 || error.status === 429);
  const told = error instanceof DashboardError && !gated;

  return (
    <Page>
      <PageHead title="Videos" />

      {gated ? null : <Filters band={band} />}

      {!data && told ? (
        <RefusalNotice error={error} variant="notice" id="filter-refused" />
      ) : !data && error !== undefined ? (
        <ReadFailure error={error} onRetry={library.reload} />
      ) : (
        <Body ready={data !== undefined} height="40dvh">
          {data ? <Table band={band} data={data} offset={params.get("offset") ?? ""} /> : null}
        </Body>
      )}
    </Page>
  );
}

function Table({ band, data, offset }: { band: Band; data: Library; offset: string }) {
  const [sortedCol, sortedDir] = SORTED[data.order] ?? [null, null];
  const carried = carriedOf(band, offset);
  const sort = (col: string) => (sortedCol === col ? sortedDir : null);

  return (
    <>
      <Notes notes={data.notes} />

      {data.videos.length ? (
        <>
          <TableCount
            shown={data.videos.length}
            total={data.total}
            hasMore={data.pagination.has_more}
          />

          <div className={table.tablewrap}>
            <table className={`${table.grid} ${styles.videos}`}>
              <caption className={ui.srOnly}>
                Videos in the corpus, with the state of each pipeline leg
              </caption>
              <thead>
                <tr>
                  <th scope="col" className={styles.colShot}>
                    <span className={ui.srOnly}>Frame</span>
                  </th>
                  <SortHead label="Title" order="title" carried={carried} sort={sort("title")} />
                  <SortHead
                    label="Published"
                    order="recency"
                    carried={carried}
                    sort={sort("published")}
                  />
                  <SortHead
                    label="Duration"
                    order="duration"
                    carried={carried}
                    num
                    sort={sort("duration")}
                  />
                  <th scope="col">State</th>
                  <th scope="col">Tags</th>
                  <SortHead
                    label="Indexed"
                    order="indexed_at"
                    carried={carried}
                    sort={sort("indexed")}
                  />
                </tr>
              </thead>
              <tbody>
                {data.videos.map((row) => (
                  <Row key={row.video_id} row={row} />
                ))}
              </tbody>
            </table>
          </div>

          <Pager
            limit={data.pagination.limit}
            offset={data.pagination.offset}
            hasMore={data.pagination.has_more}
            href={(next) => linkTo(carried, { offset: String(next) })}
          />
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
  // A new order starts the set again, so the offset goes.
  return (
    <th scope="col" className={num ? table.num : undefined} aria-sort={sort ?? undefined}>
      <DashLink href={linkTo(carried, { order, offset: null })}>{label}</DashLink>
    </th>
  );
}

function Row({ row }: { row: LibraryRow }) {
  const href = `${ROOT}/videos/${encodeURIComponent(row.video_id)}`;
  return (
    <tr>
      <td className={`${styles.colShot} ${styles.wide}`} data-label="Frame">
        {row.thumb ? (
          // A signed `/frames/…` URL already sized; the optimizer would cache it
          // past its signature.
          // eslint-disable-next-line @next/next/no-img-element
          <img
            className={ui.thumb}
            src={row.thumb}
            alt=""
            width={96}
            height={54}
            loading="lazy"
            decoding="async"
          />
        ) : (
          <span className={`${ui.thumb} ${ui.thumbEmpty}`}>no frame</span>
        )}
      </td>
      <th scope="row" className={styles.colTitle} data-label="Title">
        <DashLink className={ui.rowTitle} href={href}>
          {row.title}
        </DashLink>
        <span className={ui.rowMeta}>
          {row.channel}
          <span className={styles.wide}>
            <Sep /> <code>{row.video_id}</code>
          </span>
        </span>
      </th>
      <td className={styles.wide} data-label="Published">
        <time className={ui.nowrap}>{day(row.published_at)}</time>
      </td>
      {/* A runtime is a clock, the same string the detail page prints. */}
      <td className={`${table.num} ${styles.wide} ${ui.nowrap}`} data-label="Duration">
        {hms(row.duration_s)}
      </td>
      <td className={styles.colState} data-label="State">
        <Index row={row} />
      </td>
      <td className={styles.wide} data-label="Tags">
        {row.tags.length ? (
          <span className={styles.taglist}>
            {row.tags.map((tag) => (
              <DashLink
                key={tag}
                className={`${ui.chip} ${styles.chipSmall}`}
                href={`${ROOT}/videos?tags=${encodeURIComponent(tag)}&index_state=all`}
              >
                {tag}
              </DashLink>
            ))}
          </span>
        ) : (
          <span className={ui.muted}>—</span>
        )}
      </td>
      <td data-label="Indexed">
        <time className={ui.nowrap}>{day(row.indexed_at)}</time>
      </td>
    </tr>
  );
}

/** Quiet when fine: a ready video with every leg is the word `ready`, muted.
 *  Anything else is its pill, then each missing leg by name (§28.6). */
function Index({ row }: { row: LibraryRow }) {
  const missing = COVERAGE.filter(([key]) => !row.coverage[key]);
  if (row.index_state === "ready" && !missing.length) {
    return <span className={styles.fine}>ready</span>;
  }
  return (
    <span className={styles.index}>
      {row.index_state === "ready" ? (
        <span className={styles.fine}>ready</span>
      ) : (
        <Pill state={row.index_state} />
      )}
      {missing.map(([key, word, label]) => (
        <span className={styles.gap} key={key} title={`${label} missing`}>
          no {word}
        </span>
      ))}
    </span>
  );
}

/** No rows: paged off the end, or filtered to nothing. */
function Empty({ band, carried, data }: { band: Band; carried: URLSearchParams; data: Library }) {
  if (data.pagination.last_offset !== undefined) {
    return (
      <Notice
        id="past-end"
        title={`That page is past the end of ${count(data.total)} matching video(s).`}
        next={
          <DashLink href={linkTo(carried, { offset: String(data.pagination.last_offset) })}>
            Go to the last page
          </DashLink>
        }
      />
    );
  }

  const dated =
    band.published_after || band.published_before || band.indexed_after || band.indexed_before;
  return (
    <Notice
      id="no-match"
      title="Nothing matches those filters."
      detail={
        dated ? (
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
        )
      }
      next={<DashLink href={`${ROOT}/videos`}>Show everything</DashLink>}
    />
  );
}
