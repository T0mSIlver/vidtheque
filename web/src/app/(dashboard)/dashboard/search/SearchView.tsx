"use client";

import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState, type ChangeEvent } from "react";
import { ContentType, type Readiness, type SearchResponse } from "@/lib/dashboard/schemas";
import { dashboard, DashboardError, ROOT } from "@/lib/dashboard/client";
import { pick } from "@/lib/dashboard/query";
import { useResource } from "@/lib/dashboard/resource";
import { ChannelPick } from "@/components/dashboard/ChannelPick";
import { FrameOverlay, type Shot } from "@/components/dashboard/FrameOverlay";
import controls from "@/components/dashboard/kit/controls.module.css";
import { FilterBand } from "@/components/dashboard/kit/FilterBand";
import { notice, ReadFailure, RefusalNotice } from "@/components/dashboard/kit/notice";
import { Fold } from "@/components/dashboard/kit/Fold";
import { Notes, Pager } from "@/components/dashboard/kit/table";
import { Body, DashLink, Page, PageHead, Sep, ui, Unbroken } from "@/components/dashboard/kit/ui";
import { Moment, momentKey } from "./Moment";
import { legsOf } from "./parts";
import styles from "./search.module.css";

// Owner search over the corpus (dashboard.md §14, §14.2): the facade's own
// handler and schemas behind the dashboard gate. The filters are the URL, with
// the handler's own parameter names.

const FILTERS = ["q", "content_type", "channel"] as const;

// No control of their own, so every navigation carries them; `video_id` is a
// filter the handler takes, and dropping it would silently widen the search.
const CARRIED = ["limit", "max_text_chars", "video_id"] as const;

const PAGE_KEYS = [...FILTERS, ...CARRIED, "offset"];

/** What the query box accepts, and what a pager link is cut to. */
const CAPS: Record<string, number> = { q: 512, channel: 128 };

// `content_type` stays the URL's word; the picker shows the badges' words.
const CONTENT_WORDS: Record<ContentType, string> = {
  all: "everything",
  transcript: "spoken",
  ocr: "on-screen text",
  frame: "frames",
};

/** How long a search runs before the page asks the worker why (§28.5). */
const SLOW_MS = 1500;
const ASK_EVERY_MS = 3000;

const DEFAULTS: Record<string, string> = { content_type: "all" };

export function SearchView() {
  const params = useSearchParams();
  // `q` present at all is a search; an empty one is the handler's to refuse.
  const searched = params.has("q");
  const query = pick(params, PAGE_KEYS, { keepEmpty: ["q"] });
  const key = searched ? `search?${query}` : "search:none";
  const search = useResource(key, (signal) =>
    searched ? dashboard.search(query, signal) : Promise.resolve(null),
  );
  const page = search.data ?? null;
  // Marks are made against the query as the box accepts it, not the echo.
  const marked = (params.get("q") ?? "").slice(0, CAPS.q);

  const refusal = searched && !page ? search.error : undefined;
  // The gate's two refusals are the shell's: no band over a page this browser
  // cannot read. Every other refusal prints where the results would be.
  const gated =
    refusal instanceof DashboardError && (refusal.status === 401 || refusal.status === 429);
  const told = refusal instanceof DashboardError && !gated;

  return (
    <Page>
      <PageHead title="Search" />

      {gated ? null : <Filters params={params} />}

      {searched && !page && refusal === undefined ? <Waiting key={key} /> : null}

      {told ? <RefusalNotice error={refusal} variant="notice" id="search-refused" /> : null}
      {refusal !== undefined && !told ? (
        <ReadFailure error={refusal} onRetry={search.reload} />
      ) : null}

      {searched && (page || refusal === undefined) ? (
        <Body ready={Boolean(page)} height="60dvh">
          {page ? <Results page={page} query={marked} params={params} /> : null}
        </Body>
      ) : null}
    </Page>
  );
}

/** What a running search is waiting on, by the second. Past `SLOW_MS` the
 *  worker is asked whether its embedding models are loaded: a cold one is the
 *  usual reason a search takes tens of seconds. */
function Waiting() {
  const [elapsed, setElapsed] = useState(0);
  const [readiness, setReadiness] = useState<Readiness | null>(null);

  useEffect(() => {
    const started = Date.now();
    const tick = setInterval(() => setElapsed(Date.now() - started), 1000);
    const controller = new AbortController();
    let asking: ReturnType<typeof setTimeout>;
    const ask = () => {
      dashboard.readiness(controller.signal).then(
        (read) => setReadiness(read.readiness),
        () => undefined,
      );
      asking = setTimeout(ask, ASK_EVERY_MS);
    };
    asking = setTimeout(ask, SLOW_MS);
    return () => {
      clearInterval(tick);
      clearTimeout(asking);
      controller.abort();
    };
  }, []);

  const why = waitingOn(readiness);
  const seconds = Math.floor(elapsed / 1000);
  return (
    <div className={styles.waiting} role="status">
      <span className={styles.waitbar} aria-hidden="true" />
      <p className={`${styles.waitline} ${why.slow ? styles.waitSlow : ""}`}>
        {why.words}
        {seconds ? (
          <>
            <Sep /> <span className={ui.mono}>{seconds} s</span>
          </>
        ) : null}
      </p>
    </div>
  );
}

/** The worker's answer in words; the projection's `null` worker says nothing. */
export function waitingOn(readiness: Readiness | null): { words: string; slow: boolean } {
  const worker = readiness?.worker;
  if (!worker) return { words: "Searching", slow: false };
  if (worker.state !== "ready") return { words: "Embedding worker not answering", slow: true };
  const cold = worker.models.some(
    (model) => (model.task === "embed" || model.task === "image_embed") && !model.loaded,
  );
  return cold
    ? { words: "Loading the search models", slow: true }
    : { words: "Searching", slow: false };
}

/** Which slice of the ranking is shown: the payload's own offset, `~` when
 *  the pool ran out before the count. */
function Slice({ page }: { page: SearchResponse }) {
  const { offset, approx_total, pool_exhausted } = page.pagination;
  const shown = page.results.length;
  return (
    <>
      <Unbroken>
        <span className={ui.mono}>{shown ? offset + 1 : 0}</span>
        <Sep>–</Sep>
        <span className={ui.mono}>{offset + shown}</span>
      </Unbroken>
      {approx_total !== null && approx_total !== undefined ? (
        <>
          {" "}
          <Unbroken>
            of {pool_exhausted ? "~" : ""}
            <span className={ui.mono}>{approx_total}</span>
          </Unbroken>
        </>
      ) : null}
    </>
  );
}

function Filters({ params }: { params: URLSearchParams }) {
  const value = (key: string, fallback = "") => params.get(key) ?? fallback;
  const values = {
    q: value("q"),
    content_type: value("content_type", "all"),
    channel: value("channel"),
  };

  function toUrl(form: FormData) {
    const next = new URLSearchParams();
    for (const key of FILTERS) {
      const entry = form.get(key);
      if (typeof entry !== "string") continue;
      const chosen = entry.trim();
      // A cleared `q` is still a search, refused by the handler by name.
      if (key === "q") next.set(key, chosen);
      else if (chosen && chosen !== DEFAULTS[key]) next.set(key, chosen);
    }
    for (const key of CARRIED) {
      const carried = params.get(key)?.trim();
      if (carried && !next.has(key)) next.set(key, carried);
    }
    return `${ROOT}/search?${next}`;
  }

  // A picker reruns the search it narrows; before any query it only waits.
  const rerun = (event: ChangeEvent<HTMLElement>) => {
    // React's change is also every keystroke in the query box.
    if (!(event.target instanceof HTMLSelectElement)) return;
    const form = event.target.form;
    const query = form?.elements.namedItem("q");
    if (query instanceof HTMLInputElement && query.value.trim()) form?.requestSubmit();
  };

  return (
    <div className={styles.band} onChange={rerun}>
      <FilterBand role="search" values={values} toUrl={toUrl}>
        <div className={`${controls.field} ${styles.query}`}>
          <label className={ui.srOnly} htmlFor="search-q">
            Query
          </label>
          <input
            // The caret starts here, so changing the query needs no re-aim.
            autoFocus
            autoComplete="off"
            defaultValue={values.q}
            id="search-q"
            maxLength={CAPS.q}
            name="q"
            placeholder="a phrase from a talk, a slide, or a scene"
            required
            type="search"
          />
        </div>
        <div className={`${controls.field} ${controls.actions} ${styles.go}`}>
          <button className={controls.button} type="submit">
            Search
          </button>
        </div>
        <div className={`${controls.field} ${controls.pickField}`}>
          <label htmlFor="search-type">Look in</label>
          <span className={controls.pick}>
            <select defaultValue={values.content_type} id="search-type" name="content_type">
              {ContentType.options.map((entry) => (
                <option key={entry} value={entry}>
                  {CONTENT_WORDS[entry]}
                </option>
              ))}
            </select>
          </span>
        </div>
        <ChannelPick id="search-channel" value={values.channel} />
      </FilterBand>
    </div>
  );
}

function Results({
  page,
  query,
  params,
}: {
  page: SearchResponse;
  query: string;
  params: URLSearchParams;
}) {
  const legs = legsOf(page.leg_counts);
  // State, so closing the overlay releases the enlarged frame.
  const [shot, setShot] = useState<Shot | null>(null);
  const close = useCallback(() => setShot(null), []);
  const open = useCallback((next: Shot) => setShot(next), []);
  const { limit, offset, has_more } = page.pagination;

  return (
    <section className={styles.results} aria-labelledby="search-results">
      <h2 className={ui.srOnly} id="search-results">
        Results
      </h2>

      <div className={styles.resultsHead}>
        <p className={styles.slice}>
          <Slice page={page} />
        </p>
        {/* Three units, not summands (tool-surface.md §9.2): owner inspection
            (§14), folded at every width (§24.4). */}
        {legs.length ? (
          <Fold label="how it matched">
            <dl className={styles.legline} aria-label="Search legs">
              {legs.map((leg) => (
                <div className={`${styles.leg} ${leg.sub ? styles.legSub : ""}`} key={leg.key}>
                  <dt className={styles.legLabel}>
                    {leg.label} <span className={styles.legKey}>{leg.key}</span>
                  </dt>
                  <dd className={styles.legCount}>
                    {leg.count}
                    {leg.unit ? <span className={styles.legUnit}> {leg.unit}</span> : null}
                  </dd>
                </div>
              ))}
            </dl>
          </Fold>
        ) : null}
      </div>

      <Notes notes={page.notes} className={styles.notes} label="Search notes" />

      {page.results.length ? (
        <>
          {/* One row per hit in server rank, never grouped (dashboard.md §14.1). */}
          <ol className={styles.hits} start={offset + 1}>
            {page.results.map((hit) => (
              <Moment hit={hit} key={momentKey(hit)} onOpen={open} query={query} />
            ))}
          </ol>
          <Pager
            limit={limit}
            offset={offset}
            hasMore={has_more}
            href={(next) => linkTo(params, { offset: String(next) })}
            next="More results →"
            label="Search pagination"
          />
          <FrameOverlay onClose={close} shot={shot} />
        </>
      ) : (
        <Empty status={page.data_status} type={page.content_type} params={params} />
      )}
    </section>
  );
}

/** "Nothing matched" and "nothing is indexed" are different screens;
 *  `data_status` arrives only on the empty path to tell them apart. */
function Empty({
  status,
  type,
  params,
}: {
  status: string | null;
  type: ContentType;
  params: URLSearchParams;
}) {
  return (
    <div className={styles.empty}>
      <p className={styles.emptyLead}>
        {status === "empty" ? "Nothing is indexed in this corpus yet." : "No moments matched."}
      </p>
      <p className={ui.emptyNote}>
        {status === "empty" ? (
          <>
            <code>index-video</code> puts the first one in.
          </>
        ) : (
          "Change the query, the channel, or where to look."
        )}
      </p>
      {status !== "empty" && type !== "all" ? (
        <p className={notice.next}>
          <DashLink
            className={controls.ghostlink}
            href={linkTo(params, { content_type: null, offset: null })}
          >
            Look in everything
          </DashLink>
        </p>
      ) : null}
    </div>
  );
}

/** This URL with some parameters changed (`null` removes one), text cut to
 *  what its box accepts so a pager link works more than once. */
function linkTo(params: URLSearchParams, changes: Record<string, string | null>): string {
  const next = pick(params, PAGE_KEYS, { keepEmpty: ["q"] });
  for (const [key, cap] of Object.entries(CAPS)) {
    const value = next.get(key);
    if (value !== null) next.set(key, value.slice(0, cap));
  }
  for (const [key, value] of Object.entries(changes)) {
    if (value === null) next.delete(key);
    else next.set(key, value);
  }
  return `${ROOT}/search?${next}`;
}
