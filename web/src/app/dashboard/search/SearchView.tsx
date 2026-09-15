"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useState, type FormEvent } from "react";
import { ContentType, type Hit, type SearchResponse } from "@/lib/api/schemas";
import { dashboard, DashboardError, ROOT } from "@/lib/dashboard/client";
import { clock, DASH } from "@/lib/format";
import { groupByVideo, type VideoGroup } from "@/lib/group";
import dash from "../dashboard.module.css";
import { FrameOverlay, type Shot } from "../FrameOverlay";
import { DashLink, PageHead, ReadFailure, Reading, Sep, Unbroken } from "../parts";
import { useRead } from "../useRead";
import { evidenceOf, highlight, insideLink, legsOf, receiptOf } from "./parts";
import styles from "./search.module.css";

// The owner's search page — `templates/search.html`, reading
// `GET /dashboard/api/search` in the browser (dashboard.md §14, §14.1, §14.2).
//
// It is the page that answers the question the corpus exists for, so it carries
// the same evidence the demo's result list does — the frame, what kind of thing
// matched, and the second it happened — in this surface's idiom rather than the
// demo's: no cards, no shadow, hairlines, and lime only on text the machine
// read off a slide.
//
// **The read is the facade's own handler**, behind the dashboard's gate and
// under `policy_for`'s caller-keyed clamps, so the schemas are `lib/api`'s and
// the grouping is `lib/group`'s: one payload, two surfaces, and a hit that
// means the same thing on both. Everything this page adds is a rendering of a
// field that payload already carries (§14.2's table), and the four derivations
// live in `./parts` beside the Python they mirror.
//
// **The filters are the URL**, exactly as they were in Jinja and for the same
// reason: a search worth keeping is a link somebody can send, the back button
// walks the queries you actually ran, and GET changes no state. The parameter
// names are the handler's too — `content_type`, not a second spelling of it —
// so a `/dashboard/search?q=…&content_type=ocr` bookmarked before this port
// opens the page it always opened.

/** Every parameter the band has a control for, in the order it asks them. */
const FILTERS = ["q", "content_type", "channel"] as const;

// The parameters with no control in the band. They belong to whoever put them
// in the URL, so every navigation carries them rather than silently widening a
// reader's page back to the default.
//
// **`video_id` is one of them, and dropping it was a filter that silently did
// not apply**: `search.run` takes it (`public/api.py:290`), the Jinja page
// reached it because `search_payload` reads the request's own query string, and
// a whitelist that quietly loses a filter is the one thing "all means all"
// forbids. It goes on the wire and the handler answers for it, with a `note:`
// if it has one.
const CARRIED = ["limit", "max_text_chars", "video_id"] as const;

// `offset` is the pager's, not the band's: changing a filter changes the set,
// and page four of the old set is not page four of the new one.
const PAGE_KEYS = [...FILTERS, ...CARRIED, "offset"];

/** What the band's two text boxes accept, and what `views._search_page_link`
 *  paged with: a query pasted out of a log is bounded by the handler, and a
 *  pager link carrying the whole of it is a link nobody can use twice. */
const CAPS: Record<string, number> = { q: 512, channel: 128 };

// The picker's own vocabulary. `content_type` is a *parameter* — the value in
// the URL stays `ocr`, and the handler never sees anything else — but the word
// in the list is the one the demo's chips use, because `ocr` is a filter an
// operator has to already know the meaning of.
const CONTENT_WORDS: Record<ContentType, string> = {
  all: "all three channels",
  transcript: "transcript (spoken)",
  ocr: "on-screen text (OCR)",
  frame: "frames (visual)",
};

// A picker resting on the value the handler would have used anyway: sending it
// puts `&content_type=all` on every link a reader copies to say nothing at all.
const DEFAULTS: Record<string, string> = { content_type: "all" };

// The dashboard's own two widths from `variants.py`'s finite vocabulary
// (dashboard.md §6.4), and the strip's quality. The payload sends the demo's
// 320 and 960 against `PUBLIC_URL`; this page builds its own instead, at the
// widths this surface already pays for and on the origin it is being read from
// — a dashboard behind a tunnel or a port map renders `PUBLIC_URL`'s absolute
// URLs against a host that resolves to nothing (§14.2, and the 2026-08-09
// incident). `/frames/*` takes the session cookie beside the signature, and a
// signed-in same-origin page carries one.
const STRIP_WIDTH = 192;
const LIGHTBOX_WIDTH = 1280;
const THUMB_QUALITY = 70;

function frameUrl(frameId: string, width: number): string {
  return `/frames/${encodeURIComponent(frameId)}.jpg?w=${width}&q=${THUMB_QUALITY}`;
}

// Which class a badge and a snippet wear, per kind of evidence. Spelled out
// rather than interpolated into the stylesheet's names: `screen` is the one
// allowed lime (The Lime Rule), and a lookup that cannot miss is how that stays
// a decision rather than a string concatenation.
const BADGE_CLASS: Record<string, string> = {
  spoken: styles.badgeSpoken,
  screen: styles.badgeScreen,
  frame: styles.badgeFrame,
  other: styles.badgeOther,
};

const SNIPPET_CLASS: Record<string, string> = {
  spoken: styles.isSpoken,
  screen: styles.isScreen,
  frame: styles.isFrame,
  mixed: "",
  other: "",
};

export function SearchView() {
  const params = useSearchParams();
  const search = params.toString();
  // `q` present at all is a search, empty or not — the same test the Jinja page
  // made. An empty `q` is a refusal the handler owns (`E_EMPTY_QUERY`), and
  // answering it here would be this page growing a rule about queries.
  const searched = params.has("q");
  const read = useCallback(
    (signal: AbortSignal) =>
      searched ? dashboard.search(apiQuery(search), signal) : Promise.resolve(null),
    [search, searched],
  );
  const state = useRead<SearchResponse | null>(read);
  const page = state.status === "ready" ? state.data : null;

  // The query the marks are made against: the URL's, cut to the length the box
  // accepts, which is the string `views._highlighted` was handed. Not the
  // payload's `query` echo — the handler echoes what it was sent, uncut, and a
  // page marking against four kilobytes of pasted log would be marking against
  // something no leg ever saw.
  const query = (params.get("q") ?? "").slice(0, CAPS.q);

  const refusal = state.status === "failed" ? state.error : null;
  // The gate's two refusals are the shell's, and they replaced the whole page
  // in Jinja too — the 401 before any view ran (`views.py:166-185`) and the 429
  // in the limiter ahead of it. There is no filter to fix behind either, and a
  // band over one is an invitation to a page this browser cannot read.
  const gated =
    refusal instanceof DashboardError && (refusal.status === 401 || refusal.status === 429);
  // Everything else that comes back from the search leg is a typed refusal the
  // reader can act on — an empty query, a content channel that does not exist,
  // the tool failing — and it prints where the results would be, in the shape
  // `search.html` printed it: the code is the heading, because on an instrument
  // that is the half a bug report quotes, and the sentence is under it.
  const told = refusal instanceof DashboardError && !gated;

  return (
    <>
      <PageHead title="Search the corpus">{page ? <Slice page={page} /> : null}</PageHead>

      <Filters search={search} />

      {state.status === "loading" && searched ? <Reading /> : null}

      {told && refusal instanceof DashboardError ? (
        <section className={dash.noticeBad} aria-labelledby="search-refused">
          <h2 className={dash.noticeBadTitle} id="search-refused">
            {refusal.code}
          </h2>
          <p className={dash.noticeDetail}>{refusal.message}</p>
          {refusal.next ? <p className={dash.noticeNext}>next: {refusal.next}</p> : null}
        </section>
      ) : null}

      {state.status === "failed" && !told ? (
        <ReadFailure error={state.error} onRetry={state.reload} />
      ) : null}

      {page ? <Results page={page} query={query} search={search} /> : null}

      {!searched && state.status !== "failed" ? (
        <div className={`${styles.empty} ${styles.emptyFirst}`}>
          <p className={styles.emptyLead}>No search has run.</p>
          <p className={dash.emptyNote}>Enter words from a transcript, slide, or scene.</p>
        </div>
      ) : null}
    </>
  );
}

/** Which slice of the ranking you are looking at, on the title's own baseline.
 *
 *  `~` when the pool was exhausted rather than counted: `has_more` over exact
 *  totals, everywhere on this surface. The numbers are the payload's own
 *  `offset` and the row count, never the URL's — a reader who asked for offset
 *  10000 on a public clamp was moved, and this line says where they landed. */
function Slice({ page }: { page: SearchResponse }) {
  const { offset, approx_total, pool_exhausted } = page.pagination;
  const shown = page.results.length;
  return (
    <>
      <Unbroken>
        <span className={dash.mono}>{shown ? offset + 1 : 0}</span>
        <Sep>–</Sep>
        <span className={dash.mono}>{offset + shown}</span>
      </Unbroken>
      {approx_total !== null && approx_total !== undefined ? (
        <>
          <Sep />{" "}
          <Unbroken>
            of {pool_exhausted ? "~" : ""}
            <span className={dash.mono}>{approx_total}</span>
          </Unbroken>
        </>
      ) : null}
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
      if (typeof entry !== "string") continue;
      const chosen = entry.trim();
      // `q` is the one control whose empty value is still a value: the handler
      // refuses it by name, and dropping it here would turn a cleared box into
      // the page that has never searched.
      if (key === "q") next.set(key, chosen);
      else if (chosen && chosen !== DEFAULTS[key]) next.set(key, chosen);
    }
    // The parameters with no control of their own ride along untouched.
    for (const key of CARRIED) {
      const carried = params.get(key)?.trim();
      if (carried && !next.has(key)) next.set(key, carried);
    }
    router.push(`${ROOT}/search?${next}`);
  }

  return (
    <form className={dash.filters} key={search} onSubmit={submit} role="search">
      <div className={`${dash.field} ${dash.wide}`}>
        <label htmlFor="search-q">Query</label>
        <input
          // The caret starts in the query box, on the way in and on the way back
          // from every search. It is this page's answer to the thing the videos
          // band answers with `sessionStorage`: a search box you have to click
          // into before you can change the query makes you re-aim after every
          // result.
          autoFocus
          autoComplete="off"
          defaultValue={value("q")}
          id="search-q"
          maxLength={CAPS.q}
          name="q"
          placeholder="a phrase from a talk, a slide, or a scene"
          required
          type="search"
        />
      </div>
      {/* The channel the *corpus* is searched over, which is not the channel a
          video was published on — two different words that were both called
          `Channel` on this page until 2026-08-13. */}
      <div className={`${dash.field} ${dash.pickField}`}>
        <label htmlFor="search-type">Searched content</label>
        <span className={dash.pick}>
          <select defaultValue={value("content_type", "all")} id="search-type" name="content_type">
            {ContentType.options.map((entry) => (
              <option key={entry} value={entry}>
                {CONTENT_WORDS[entry]}
              </option>
            ))}
          </select>
        </span>
      </div>
      <div className={`${dash.field} ${dash.text}`}>
        <label htmlFor="search-channel">Video channel</label>
        <input
          autoComplete="off"
          defaultValue={value("channel")}
          id="search-channel"
          maxLength={CAPS.channel}
          name="channel"
          type="text"
        />
      </div>
      <div className={`${dash.field} ${dash.actions}`}>
        <button className={dash.ghostlink} type="submit">
          Search
        </button>
      </div>
    </form>
  );
}

function Results({ page, query, search }: { page: SearchResponse; query: string; search: string }) {
  const legs = legsOf(page.leg_counts);
  const groups = groupByVideo(page.results);
  // The frame the reader is looking at, or nothing. State and not a ref,
  // because the caption, the footer link and the picture all render from it —
  // and because setting it back to nothing is what releases the bytes.
  const [shot, setShot] = useState<Shot | null>(null);
  // Stable, because the overlay listens for the element's own `close` event and
  // a new closure every render would be a listener torn down and rebuilt.
  const close = useCallback(() => setShot(null), []);
  // Where in the ranking each moment sits. Grouping rearranges one page of
  // results and never re-ranks them, so a hit keeps the position the server
  // gave it: `<ol start="{{ offset + 1 }}">` in Jinja, and the same number on
  // the item here, because a group's hits need not be adjacent in the ranking
  // they were pulled out of.
  const rank = new Map(page.results.map((hit, index) => [hit, page.pagination.offset + index + 1]));

  return (
    <section className={dash.panel} aria-labelledby="search-results">
      <h2 className={dash.panelTitle} id="search-results">
        Results
      </h2>

      {/* What each leg contributed, as a sentence rather than as eight
          identifiers — and the tool's own key beside it, because this is an
          instrument and the key is what a bug report quotes. The three numbers
          are three units and are not summands (tool-surface.md §9.2), so no
          total is drawn under them. */}
      {legs.length ? (
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
      ) : null}

      {/* The query layer's own lines. The `note:` marker does not survive the
          facade — a JSON reader gets `humanize.notes`, prefix stripped and
          sentence untouched — and the sentence is the half that was the
          operator's (§14.2). Rendered, never composed here. */}
      {page.notes.length ? (
        <ul className={styles.notes} aria-label="Search notes">
          {page.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      ) : null}

      {groups.length ? (
        <>
          <ol className={styles.groups}>
            {groups.map((group) => (
              <Group
                group={group}
                key={group.video_id}
                onOpen={setShot}
                query={query}
                rank={rank}
              />
            ))}
          </ol>
          <Pager pagination={page.pagination} search={search} />
          <FrameOverlay onClose={close} shot={shot} />
        </>
      ) : (
        <Empty status={page.data_status} type={page.content_type} search={search} />
      )}
    </section>
  );
}

/** One video, and every moment in it that matched.
 *
 *  Ten flat hits are usually three talks (demo-site.md §6.5). The server ranks
 *  and paginates; this groups what it was handed and nothing else, so a
 *  group's position is the position of its best hit and never a re-ranking. */
function Group({
  group,
  onOpen,
  query,
  rank,
}: {
  group: VideoGroup;
  onOpen: (shot: Shot) => void;
  query: string;
  rank: Map<Hit, number>;
}) {
  return (
    <li className={styles.group}>
      <div className={styles.groupHead}>
        <p className={styles.groupTitle}>
          <DashLink href={`${ROOT}/videos/${encodeURIComponent(group.video_id)}`}>
            {group.title}
          </DashLink>
        </p>
        <p className={styles.groupMeta}>
          <span className={styles.where}>{group.channel || "unknown"}</span>
          <Sep /> <code>{group.video_id}</code>
          <Sep /> {group.hits.length} moment(s)
        </p>
      </div>
      <ol className={styles.hits}>
        {group.hits.map((hit) => (
          <Moment
            channel={hit.channel === group.channel ? null : hit.channel || "unknown"}
            hit={hit}
            key={momentKey(hit)}
            onOpen={onOpen}
            query={query}
            rank={rank.get(hit)}
          />
        ))}
      </ol>
    </li>
  );
}

function Moment({
  channel,
  hit,
  onOpen,
  query,
  rank,
}: {
  channel: string | null;
  hit: Hit;
  onOpen: (shot: Shot) => void;
  query: string;
  rank?: number;
}) {
  const evidence = evidenceOf(hit.source);
  const inside = insideLink(hit);
  const receipt = receiptOf(hit.link);
  // **The timecode is `clock(match_start)`, not `timestamp`** (§14.2).
  // `timestamp` is `clock(start)`, the segment's own opening, and for a fused
  // transcript hit that is a different second from the cue that actually
  // matched. The float is on the payload; this formats that one.
  const at = hit.match_start === null ? DASH : clock(hit.match_start);
  const runs = highlight(hit.text, query);

  return (
    // `value` and not a `start` on the list: a group's moments need not be
    // adjacent in the ranking they were grouped out of, so each one carries its
    // own position in it.
    <li className={styles.hit} value={rank}>
      {hit.frame_id ? (
        // For an OCR or a frame hit the picture *is* the evidence, so it opens
        // where the reader is rather than in a tab that has lost the ranking.
        // The same overlay the frames view opens, at the same width: one
        // component on one contract, and the caption carries the three facts a
        // frame has — its id, its second, and the talk it came out of. No
        // `lines`: what the machine read off the still is the video page's
        // layer, and a result row has no reading to show.
        <button
          aria-label={`Enlarge the frame at ${at}`}
          className={styles.shot}
          onClick={() =>
            onOpen({
              alt: `Keyframe at ${at}`,
              caption: `${hit.frame_id} · ${at} · ${hit.title}`,
              frameId: hit.frame_id as string,
              large: frameUrl(hit.frame_id as string, LIGHTBOX_WIDTH),
              link: receipt?.href ?? null,
            })
          }
          type="button"
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            alt=""
            decoding="async"
            height={72}
            loading="lazy"
            src={frameUrl(hit.frame_id, STRIP_WIDTH)}
            width={128}
          />
        </button>
      ) : (
        // No keyframe for this moment: the channel it came from, in the box the
        // frame would have taken, so the column stays a column. A spoken hit has
        // no picture and saying so is more honest than a broken one.
        <span aria-hidden="true" className={`${styles.shot} ${styles.shotEmpty}`}>
          {evidence.pills[0]?.label ?? "video"}
        </span>
      )}

      <div className={styles.body}>
        {/* Into the index, not out to YouTube: the title opens what this
            deployment stored about that video, and a frame hit lands **on the
            frame**. The group head above carries the same words pointing at the
            video plainly — two questions, two destinations — and the receipt at
            the end of the row is the third. */}
        <p className={styles.title}>
          {inside ? <DashLink href={inside}>{hit.title}</DashLink> : hit.title}
        </p>
        <p className={styles.meta}>
          {/* What kind of evidence this is, in a word, with the tool's own
              `source` on the group — the word carries the meaning and the
              colour only reinforces it, so the row reads on a monochrome screen
              and for a colour-blind reader. */}
          <span className={styles.badges} title={`source=${evidence.key}`}>
            {evidence.pills.map((pill) => (
              <span className={`${styles.badge} ${BADGE_CLASS[pill.kind]}`} key={pill.label}>
                {pill.label}
              </span>
            ))}
          </span>
          {/* The channel this moment came from, when it is not the one the
              group head already prints. Two hits filed under one `video_id`
              that disagree about their channel is a corpus fact, and the row
              that has it says so rather than inheriting the head's. */}
          {channel ? (
            <>
              <span className={styles.where}>{channel}</span>
              <Sep />
            </>
          ) : null}
          {/* Into the index, not out to YouTube: a hit with a keyframe lands on
              that frame, and a transcript hit on the video plainly, because it
              names its cues by id while the transcript panel pages by offset. */}
          {inside ? (
            <DashLink className={styles.at} href={inside}>
              {at}
            </DashLink>
          ) : (
            <span className={styles.at}>{at}</span>
          )}
        </p>

        {/* The snippet, set as what it is evidence of. Spoken text is quoted
            because a person said it; on-screen text is mono and lime because
            the machine read it off the slide; text that merely rode along with a
            frame is mono and muted, because it is not what matched. */}
        {runs.length ? (
          <p className={`${styles.snippet} ${SNIPPET_CLASS[evidence.kind]}`}>
            {runs.map((run, index) =>
              run.hit ? <mark key={index}>{run.text}</mark> : <span key={index}>{run.text}</span>,
            )}
          </p>
        ) : evidence.kind === "frame" ? (
          // **`text` is `null` on a frame hit that matched on imagery alone**,
          // because the humanising layer drops the tool's stand-in sentence
          // (§14.2). The page says so in its own words rather than rendering a
          // sentence styled as a quotation.
          <p className={`${styles.snippet} ${styles.isFrame}`}>visual match, no text hit</p>
        ) : null}
      </div>

      {/* The receipt, and it is a second anchor rather than something inside the
          first: a link inside a link is neither valid nor operable. Absent when
          the tool's link is not an HTTPS `youtu.be` with a whole second on it —
          this page never reconstructs a timestamp or invents a link. */}
      {receipt ? (
        <a className={styles.receipt} href={receipt.href} rel="noopener noreferrer" target="_blank">
          {receipt.label} ↗
        </a>
      ) : null}
    </li>
  );
}

/** Nothing matched, in the two ways that happens.
 *
 *  `data_status` is the search tool's own word for the state of the corpus and
 *  arrives only on the empty path: "nothing matched" and "nothing is indexed
 *  yet" are different screens, and a `?q=` link into a fresh instance would
 *  otherwise blame the query for an empty corpus. */
function Empty({
  status,
  type,
  search,
}: {
  status: string | null;
  type: ContentType;
  search: string;
}) {
  return (
    <div className={styles.empty}>
      <p className={styles.emptyLead}>
        {status === "empty" ? "Nothing is indexed in this corpus yet." : "No moments matched."}
      </p>
      <p className={dash.emptyNote}>
        {status === "empty" ? (
          <>
            <code>index-video</code> puts the first one in.
          </>
        ) : (
          "Change the query, channel, or searched content."
        )}
      </p>
      {status !== "empty" && type !== "all" ? (
        <p className={dash.noticeNext}>
          <DashLink
            className={dash.ghostlink}
            href={linkTo(search, { content_type: null, offset: null })}
          >
            Search all three channels
          </DashLink>
        </p>
      ) : null}
    </div>
  );
}

function Pager({
  pagination,
  search,
}: {
  pagination: SearchResponse["pagination"];
  search: string;
}) {
  const { limit, offset, has_more } = pagination;
  if (!offset && !has_more) return null;
  return (
    <nav className={dash.pager} aria-label="Search pagination">
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
          More results →
        </DashLink>
      ) : null}
    </nav>
  );
}

/** A key for a moment inside one video: the leg it came from and the second it
 *  matched, which is what makes two hits in one talk two rows. */
function momentKey(hit: Hit): string {
  return `${hit.source}-${hit.match_start ?? hit.start}-${hit.frame_id ?? ""}`;
}

/** The page's URL, filtered down to the parameters the handler takes.
 *
 *  A whitelist rather than a passthrough: this is the string that becomes a
 *  request, and an unknown key in the URL bar has no business reaching the API
 *  because somebody pasted it. Values go as typed — every clamp is Python's and
 *  caller-keyed, and one corrected here would be a bound the reader is never
 *  told about. */
export function apiQuery(search: string): URLSearchParams {
  const from = new URLSearchParams(search);
  const query = new URLSearchParams();
  for (const key of PAGE_KEYS) {
    const value = from.get(key);
    // `q` alone is sent even when empty: it is what makes this a search at all,
    // and the refusal it earns is the handler's to give.
    if (value !== null && (value.trim() || key === "q")) query.set(key, value.trim());
  }
  return query;
}

/** This page's URL with some of its parameters changed; `null` removes one.
 *
 *  The two text parameters are cut to what their own boxes accept, which is
 *  what `views._search_page_link` paged with: the handler bounds a query of its
 *  own accord, and a Next link carrying four kilobytes of pasted log is a link
 *  that only works once. */
function linkTo(search: string, changes: Record<string, string | null>): string {
  const next = apiQuery(search);
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
