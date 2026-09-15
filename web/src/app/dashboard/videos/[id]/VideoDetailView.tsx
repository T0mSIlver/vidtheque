"use client";

import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Pill } from "@/components/Pill";
import { dashboard, DashboardError, ROOT } from "@/lib/dashboard/client";
import type { Cue, FrameCard, Shot, Stage, VideoDetail } from "@/lib/dashboard/schemas";
import { at, bytes, clock, count, DASH, day, duration, hms, iso } from "@/lib/format";
import dash from "../../dashboard.module.css";
import { FrameOverlay, OcrBoxes, OcrLines } from "../../FrameOverlay";
import {
  DashLink,
  Fact,
  Figure,
  PageHead,
  Panel,
  ReadFailure,
  Reading,
  Sep,
  StatePair,
  Unbroken,
  useDocumentTitle,
  useWriteSide,
} from "../../parts";
import { useRead } from "../../useRead";
import { ReindexControl, TagsForm } from "../Manage";
import videos from "../videos.module.css";
import styles from "./detail.module.css";

// The video detail — `templates/video.html`, reading
// `GET /dashboard/api/library/{video_id}` (dashboard.md §5.3, §20).
//
// The reason the dashboard exists: what the pipeline did to one video, what it
// produced, and what it read off the screen. Nothing here has an equivalent
// anywhere in the MCP surface — `job-status` collapses the seven stages into
// five wire stages for a model's benefit, and a human wants the seven, with
// the model that produced each.
//
// Four bounds are the URL's, exactly as they were in Jinja: `frames` and
// `frame_offset` for the strip, `cues` and `cue_offset` for the transcript.
// All four are Python's numbers — `?frames=200` is clamped to 96 server-side
// and the payload's `notes` says so; `?cues=` is held under the cue endpoint's
// own `max_limit`, which the detail payload carries, and clamped again by the
// endpoint itself.
//
// What changed at the port is *how* the transcript's two are spent, not
// whether they exist. The panel appends its next batch in place rather than
// reloading the page, so `cue_offset` seeds the first read and names the first
// cue on screen — a reader who pages back to it writes it down again with
// `history.replaceState`, which is what makes a transcript position something
// you can send somebody without throwing the strip, the frames and the
// reader's own place away to reach it (dashboard.md §5.3).

const FRAME_KEYS = ["frames", "frame_offset"];
const CUE_KEYS = ["cues", "cue_offset"];

// A page of the transcript cannot start further in than the endpoint's own
// offset ceiling (`views.video_detail`'s `clamp(…, 0, 500_000, 0)`).
const CUE_OFFSET_MAX = 500_000;

// A new shot's frame waits out a short pause before it is asked for, so a
// sweep across two hundred shots is not two hundred requests.
const SETTLE_MS = 70;

// Arrow keys step shots. The anchors were already focusable and already the
// navigation, so this moves focus between them rather than inventing a
// selection model of its own.
const STEPS: Record<string, number | undefined> = {
  ArrowRight: 1,
  ArrowLeft: -1,
  ArrowDown: 1,
  ArrowUp: -1,
};

/** The shot bar an event landed in, or `null` for the band's own background. */
function barElement(target: EventTarget | null): Element | null {
  return target instanceof Element ? target.closest("[data-shot]") : null;
}

export function VideoDetailView({ videoId }: { videoId: string }) {
  const params = useSearchParams();
  const search = params.toString();
  // The read is bounded by the strip's two parameters and by nothing else on
  // this URL. `select` is on it too, and putting a frame into evidence — a
  // shot bar's click, or opening one in the lightbox — must not re-read the
  // whole video to mark a card that is already on the page.
  const bounds = frameQuery(search).toString();
  const read = useCallback(
    (signal: AbortSignal) => dashboard.video(videoId, new URLSearchParams(bounds), signal),
    [videoId, bounds],
  );
  const state = useRead(read);
  const selected = selectedOrd(params.get("select"));

  if (state.status === "loading") return <Reading />;

  if (state.status === "failed") {
    // An id that is not in the corpus is not a failure to read the instance:
    // the read succeeded and the answer is "there is no such video". It gets
    // the refusal's own words and a way back to the table, not the retry
    // button, because retrying will produce this answer again.
    const refusal = state.error;
    if (refusal instanceof DashboardError && refusal.status === 404) {
      return <UnknownVideo refusal={refusal} videoId={videoId} />;
    }
    return (
      <>
        <Crumbs videoId={videoId} />
        <PageHead title="Video" />
        <ReadFailure error={state.error} onRetry={state.reload} />
      </>
    );
  }

  return (
    <Loaded
      data={state.data}
      search={search}
      selected={selected}
      seedSize={cueBound(params.get("cues"), state.data.transcript.max_limit, 1)}
      seedOffset={cueBound(params.get("cue_offset"), CUE_OFFSET_MAX, 0) ?? 0}
    />
  );
}

/**
 * An id that is not in the corpus. Not a failure to read the instance: the read
 * succeeded and the answer is "there is no such video", so it gets the
 * refusal's own words and a way back to the table rather than a retry button —
 * retrying will produce this answer again.
 *
 * A component of its own for the name: `views.video_detail` gave this document
 * the title "Unknown video", and a hook cannot be called from the branch of a
 * render that returns early.
 */
function UnknownVideo({ refusal, videoId }: { refusal: DashboardError; videoId: string }) {
  useDocumentTitle("Unknown video");
  return (
    <>
      <Crumbs videoId={videoId} />
      <PageHead title="Unknown video" />
      <section className={dash.noticeBad} aria-labelledby="unknown">
        <h2 className={dash.noticeBadTitle} id="unknown">
          {refusal.message}
        </h2>
        <p className={dash.noticeDetail}>
          <code>{refusal.code}</code>
        </p>
        {refusal.next ? <p className={dash.noticeNext}>{refusal.next}</p> : null}
        <p className={dash.noticeNext}>
          <DashLink href={`${ROOT}/videos`}>Back to the videos table</DashLink>
        </p>
      </section>
    </>
  );
}

function Loaded({
  data,
  search,
  selected,
  seedSize,
  seedOffset,
}: {
  data: VideoDetail;
  search: string;
  selected: number | null;
  seedSize: number | null;
  seedOffset: number;
}) {
  const { video, counts, frames, shots } = data;
  const runtime = video.duration_s ?? 0;
  const failed = data.stages.filter((stage) => stage.state === "failed");
  // The tags the row carries *after* a write, when there has been one. Read
  // back by Python rather than derived from what was asked for: `tag_video`
  // reports what it added and removed across a batch, and both places this page
  // prints tags are showing the row instead.
  const [written, setWritten] = useState<string[] | null>(null);
  const tags = written ?? video.tags;

  // The frame the lightbox is showing, and the frame that is in evidence.
  //
  // They are two facts, not one: a shot bar puts a keyframe into evidence
  // without opening it — the strip scrolls to that moment and the card is
  // marked — and the second click, on the frame the reader can now see, is the
  // one that opens it. Opening also marks, because a frame you are reading is
  // the frame you are on.
  const [open, setOpen] = useState<FrameCard | null>(null);

  // The shot under the pointer, from either end. The timeline and the strip
  // are two views of one thing, and the link between them is otherwise
  // invisible.
  const [linked, setLinked] = useState<number | null>(null);

  // Stable, because the overlay listens for the element's own `close` event
  // and a new closure every render would be a listener torn down and rebuilt.
  const closeFrame = useCallback(() => setOpen(null), []);

  function openFrame(frame: FrameCard) {
    setOpen(frame);
    // The same address a shot bar would have produced, minus the navigation:
    // `replaceState` is read back by `useSearchParams`, so `?select=` stays
    // the one place the marked frame is written down and the card's mark falls
    // out of it. A browser that refuses the rewrite still has the frame on
    // screen; the URL is the bookmark, not the state.
    try {
      const here = new URL(window.location.href);
      here.searchParams.set("select", String(frame.ord));
      here.hash = `frame-${frame.ord}`;
      window.history.replaceState(null, "", here.href);
    } catch {
      // Nothing to do and nothing lost.
    }
  }

  /**
   * Put a keyframe into evidence without leaving the page.
   *
   * `dashboard.js`'s `selectFrame`, and the reason it existed is the keyboard
   * path: a shot bar is a real link to `#frame-N` on the strip page that holds
   * it, and following it reloaded a whole page to move a mark and then left the
   * reader's focus at the top of it. The card is already on screen most of the
   * time — a video has one shot per keyframe and a strip page holds twenty-four
   * of them — so the click is intercepted, the mark moves, the strip scrolls to
   * that moment and focus lands on the frame's own button, which is the control
   * the next Enter should open.
   *
   * The mark itself is `?select=`, written with `replaceState` exactly as
   * `openFrame` writes it: one place says which frame is marked, so a reload, a
   * copied link and the back button all agree, and clearing the previous mark
   * is the same act as setting this one.
   *
   * `false` when the card is not on this page of the strip — a bar can point at
   * a frame twenty pages along — and the caller then lets the link navigate,
   * which is the path that was always there and is still the path with this
   * file blocked.
   */
  const selectFrame = useCallback((ord: number): boolean => {
    const card = document.getElementById(`frame-${ord}`);
    if (!card) return false;
    try {
      const here = new URL(window.location.href);
      here.searchParams.set("select", String(ord));
      here.hash = `frame-${ord}`;
      window.history.replaceState(null, "", here.href);
    } catch {
      // A browser that refuses the rewrite still has the selection on screen;
      // the URL is the bookmark, not the state.
    }
    card.scrollIntoView({ block: "center", behavior: "smooth" });
    card.querySelector<HTMLButtonElement>("button")?.focus({ preventScroll: true });
    return true;
  }, []);

  // Where the reader is in the transcript, written down where a link can carry
  // it. The same mechanism `openFrame` uses and for the same reason: the URL is
  // the bookmark, not the state — nothing on this page reads these two back, so
  // a browser that refuses the rewrite loses a link and not a panel.
  //
  // Neither key is *introduced* by paging. `cue_offset` appears once the reader
  // has actually moved off the top, and `cues` only where they typed it, so the
  // ordinary address stays the address they arrived at.
  const onWhere = useCallback((offset: number, limit: number) => {
    try {
      const here = new URL(window.location.href);
      const position = offset > 0 || here.searchParams.has("cue_offset");
      const sized = here.searchParams.has("cues");
      if (!position && !sized) return;
      if (position) here.searchParams.set("cue_offset", String(offset));
      if (sized) here.searchParams.set("cues", String(limit));
      here.hash = "transcript";
      if (here.href === window.location.href) return;
      window.history.replaceState(null, "", here.href);
    } catch {
      // Nothing to do and nothing lost.
    }
  }, []);

  // The document is named after data the browser is holding and the server
  // never saw: this shell is served without the session cookie, so `metadata`
  // in `page.tsx` cannot know the title. It is set once the read lands, which
  // is the only moment it is knowable.
  useEffect(() => {
    document.title = `${video.title} — vidtheque`;
  }, [video.title]);

  return (
    <>
      <Crumbs videoId={video.video_id} />

      {/* Every separator is glued to the fact *before* it and followed by a
          real space. JSX drops the whitespace between two elements when it
          holds a newline, so without the explicit `{" "}` these strips have no
          break opportunity at all and a header runs 56px off a 390px screen
          (measured, 2026-09-05). */}
      <PageHead title={video.title}>
        <Unbroken>
          <StatePair label="index_state" word={video.index_state} />
        </Unbroken>{" "}
        {/* `video-summary`'s own word, verbatim, and only when it says
            something `index_state` did not — §4.5 keeps the four vocabularies
            apart, so a bare `ready` beside a bare `no_frames` would read as the
            page contradicting itself. Naming both sources turns a
            contradiction into two facts. */}
        {data.data_status && data.data_status !== video.index_state ? (
          <Unbroken>
            <StatePair label="data_status" word={data.data_status} />
          </Unbroken>
        ) : null}
      </PageHead>

      <p className={styles.facts}>
        {video.channel}
        <Sep /> <Fact label="published" value={day(video.published_at)} />
        <Sep />{" "}
        {/* `h:mm:ss`, not `8m 00s`: this is the length of the thing every
            other clock on the page is an offset into — the shot band's ticks,
            a cue's timecode, a chapter's start — and a runtime spelled in a
            second form is a number the reader has to convert before it can be
            compared to any of them. `text.duration_clock`'s shape, which is
            what Jinja printed here. */}
        <Unbroken>
          <span className={dash.mono}>{hms(video.duration_s)}</span>
        </Unbroken>
        {video.language ? (
          <>
            <Sep />{" "}
            <Unbroken>
              <span className={dash.mono}>{video.language}</span>
            </Unbroken>
          </>
        ) : null}
        <Sep />{" "}
        {video.indexed_at ? (
          <Fact label="indexed" value={at(video.indexed_at)} />
        ) : (
          <Unbroken>never finished indexing</Unbroken>
        )}
      </p>

      <p className={styles.facts}>
        <a href={video.url} rel="noopener noreferrer" target="_blank">
          Open on YouTube
        </a>
        {tags.length ? (
          <>
            <Sep />{" "}
            <span className={styles.taglist}>
              {tags.map((tag) => (
                <DashLink
                  key={tag}
                  className={dash.chip}
                  href={`${ROOT}/videos?tags=${encodeURIComponent(tag)}&index_state=all`}
                >
                  {tag}
                </DashLink>
              ))}
            </span>
          </>
        ) : null}
        <QueueChannel url={video.url} />
      </p>

      <Notes notes={data.notes} />

      {/* The refusal `video-summary` answered with, in its own words. It is
          why the panels below are thin, so it is a fact about the video rather
          than a failure of this page's read. */}
      {data.summary_error ? (
        <section className={dash.notice} aria-labelledby="summary-refused">
          <h2 className={dash.noticeTitle} id="summary-refused">
            {data.summary_error.message}
          </h2>
          <p className={dash.noticeDetail}>
            <code>{data.summary_error.code}</code>
          </p>
          {data.summary_error.next ? (
            <p className={dash.noticeNext}>{data.summary_error.next}</p>
          ) : null}
        </section>
      ) : null}

      {/* The root cause, named where the eye already is. The stage that failed
          is in a table that on a phone is a five-column grid inside a sideways
          scroller, so "this video is mid-pipeline" was legible and "stt failed"
          was two swipes away. */}
      {failed.length ? (
        <p className={styles.alarm}>
          <Pill state="failed" />{" "}
          {failed.map((stage, index) => (
            <span key={stage.stage}>
              <code>{stage.stage}</code>
              {index < failed.length - 1 ? ", " : ""}
            </span>
          ))}{" "}
          did not finish. <a href="#provenance">Provenance</a>.
        </p>
      ) : null}

      <Timeline
        shots={shots.shots}
        capped={shots.capped}
        runtime={runtime}
        framePage={frames.limit}
        search={search}
        videoId={video.video_id}
        linked={linked}
        onLink={setLinked}
        onSelect={selectFrame}
      />

      <Panel id="counts" title="What was stored">
        <dl className={dash.figures}>
          <Figure
            label="cues"
            notes={[
              Object.keys(data.cue_origins).length ? (
                <>
                  {/* The tally as Jinja printed it: the bare integer, not the
                      grouped one. The figure above it is the count `render.count`
                      groups; these are the same total split by origin, and
                      `whisperx 1,203` beside `1,203` reads as a second figure
                      rather than as its breakdown. */}
                  {Object.entries(data.cue_origins).map(([origin, n], index) => (
                    <span key={origin}>
                      {index ? " · " : ""}
                      {origin} {n}
                    </span>
                  ))}
                </>
              ) : (
                <>none</>
              ),
            ]}
          >
            {count(counts.cues)}
          </Figure>
          <Figure label="chunks" notes={[<>from {count(counts.cues)} cues</>]}>
            {count(counts.chunks)}
          </Figure>
          <Figure label="keyframes" notes={[<>kept of {count(counts.keyframes)} captured</>]}>
            {count(counts.keyframes_kept)}
          </Figure>
          <Figure label="frames with text" notes={[<>{count(counts.ocr_lines)} lines read</>]}>
            {count(counts.ocr_frames)}
          </Figure>
          <Figure label="chapters" notes={[<>from the source metadata</>]}>
            {count(counts.chapters)}
          </Figure>
          <Figure
            label="keyframe bytes"
            notes={[
              counts.cues_with_words ? (
                <>word timings on {count(counts.cues_with_words)} cues</>
              ) : (
                <>no word timings stored</>
              ),
            ]}
          >
            {bytes(counts.jpeg_bytes)}
          </Figure>
        </dl>
      </Panel>

      <Provenance stages={data.stages} />

      <Frames
        frames={frames}
        search={search}
        selected={selected}
        videoId={video.video_id}
        linked={linked}
        onLink={setLinked}
        onOpen={openFrame}
      />

      <Transcript
        key={video.video_id}
        onWhere={onWhere}
        search={search}
        seedOffset={seedOffset}
        seedSize={seedSize}
        transcript={data.transcript}
        videoId={video.video_id}
      />

      {data.chapters.length ? (
        <Panel id="chapters" title="Chapters">
          <ol className={styles.chapters}>
            {data.chapters.map((chapter) => (
              <li className={styles.chapter} key={`${chapter.start_s}-${chapter.title}`}>
                {/* The chapter's own start, not the payload's `link`. That URL
                    is `deeplink()`'s, which subtracts `DEEPLINK_LEAD` (§3.6) —
                    a lead that exists so a *quoted moment* is not missed by a
                    second, and a chapter start is not a moment, it is a
                    boundary. Two seconds before it is the previous chapter,
                    and the number under the pointer would not be the number
                    the reader lands on. Jinja built this href from
                    `chapter.start` for the same reason. */}
                <a
                  className={styles.at}
                  href={`https://youtu.be/${encodeURIComponent(video.video_id)}?t=${Math.floor(chapter.start_s)}`}
                  rel="noopener noreferrer"
                  target="_blank"
                >
                  {clock(chapter.start_s)}
                </a>
                <span>{chapter.title}</span>
              </li>
            ))}
          </ol>
        </Panel>
      ) : null}

      <JobHistory history={data.job_history} />

      <Manage video={video} tags={tags} onWritten={setWritten} />

      {/* The overlay the search results open too, with the machine's reading
          of the frame as this page's own layer over it. */}
      <FrameOverlay onClose={closeFrame} shot={open ? frameShot(open, video.video_id) : null} />
    </>
  );
}

/**
 * One frame card, as the overlay describes a frame.
 *
 * The caption is the three facts a keyframe has — its id, its second and, when
 * the store recorded them, its size on disk — and the quiet line under it is
 * the pipeline's own reading of it. `lines` is present even when the machine
 * read nothing off the still: it is what asks for the OCR layer, and a frame
 * with no text is the same overlay with an empty list, not a different one.
 */
function frameShot(frame: FrameCard, videoId: string) {
  const dims = frame.width && frame.height ? ` · ${frame.width}×${frame.height}` : "";
  const size = frame.jpeg_bytes === null ? "" : ` · ${bytes(frame.jpeg_bytes)}`;
  const state =
    frame.dup_of_ord !== null
      ? `duplicate of #${frame.dup_of_ord}`
      : `sharpness ${frame.sharpness === null ? DASH : frame.sharpness.toFixed(1)}`;
  const read = frame.lines.length ? ` · ${frame.lines.length} line(s)` : "";
  return {
    alt: `Keyframe ${frame.ord} at ${clock(frame.t_s)}`,
    caption: `${frame.frame_id} · ${clock(frame.t_s)}${dims}${size}`,
    facts: `shot ${frame.shot_id} · ${state} · ${frame.ocr_state}${read}`,
    file: frame.large,
    frameId: frame.frame_id,
    large: frame.large,
    lines: frame.lines,
    link: `https://youtu.be/${videoId}?t=${Math.floor(frame.t_s)}`,
  };
}

/**
 * A `GET` prefill, not a write: the video's own URL encoded into one internal
 * link, with the expansion that means "the rest of this channel".
 *
 * The index form remains the place the operator reviews it and the `POST`
 * remains the only state change — which is why this is a link and not a button,
 * and why it is the same two parameters `_prefilled_index_form` reads.
 *
 * Only where the write side is registered: it points at a page that is not
 * there otherwise.
 */
function QueueChannel({ url }: { url: string }) {
  const { rendered } = useWriteSide();
  if (!rendered) return null;
  const query = new URLSearchParams({ urls: url, expand: "channel_recent" });
  return (
    <>
      <Sep /> <DashLink href={`${ROOT}/index?${query}`}>Queue more from this channel</DashLink>
    </>
  );
}

/**
 * The write side, last on the page and only where it is registered (§2.4).
 *
 * Two actions and no third: `jobs.kind='delete'` is in the schema with no
 * pipeline behind it, so a delete button here would queue a job that fails
 * (§5.2).
 *
 * The section carries the `manage` id itself rather than through `Panel`,
 * because the videos table's Tag link is a fragment pointing at it: a row with
 * no room for two text fields sends the reader to the one place that has them.
 */
function Manage({
  video,
  tags,
  onWritten,
}: {
  video: VideoDetail["video"];
  tags: string[];
  onWritten: (tags: string[]) => void;
}) {
  const { rendered, indexable } = useWriteSide();
  if (!rendered) return null;
  return (
    <section className={dash.panel} id="manage" aria-labelledby="manage-title">
      <h2 className={dash.panelTitle} id="manage-title">
        Manage this video
      </h2>

      <div className={dash.split}>
        <div className={videos.manageAction}>
          <h3 className={videos.label}>Re-index</h3>
          <p className={dash.emptyNote}>
            Runs <code>index-video</code> with <code>force_reindex</code> on this one URL: every
            stage runs again, and the old rows are invalidated first.
          </p>
          <ReindexControl label="Re-index this video" primary videoId={video.video_id} />
          {indexable ? null : (
            <p className={videos.manageNote}>
              Indexing is refused on this instance: the corpus config and the vector tables
              disagree.
            </p>
          )}
        </div>

        <div className={videos.manageAction}>
          <h3 className={videos.label}>Tags</h3>
          <p className={dash.emptyNote}>
            <code>namespace:value</code>, lowercase, comma separated. Ten per field.
          </p>
          <TagsForm videoId={video.video_id} tags={tags} onWritten={onWritten} />
          {tags.length ? (
            <p className={videos.manageNote}>
              On this video now:{" "}
              {tags.map((tag, index) => (
                <span key={tag}>
                  <code>{tag}</code>
                  {index < tags.length - 1 ? ", " : ""}
                </span>
              ))}
              .
            </p>
          ) : null}
        </div>
      </div>
    </section>
  );
}

function Crumbs({ videoId }: { videoId: string }) {
  return (
    <p className={styles.crumbs}>
      <DashLink href={`${ROOT}/videos`}>Videos</DashLink> <span aria-hidden="true">/</span>{" "}
      <code>{videoId}</code>
    </p>
  );
}

/** What a clamp moved. Policy text, rendered rather than composed. */
function Notes({ notes }: { notes: string[] }) {
  if (!notes.length) return null;
  return (
    <ul className={styles.notes}>
      {notes.map((note) => (
        <li key={note}>{note}</li>
      ))}
    </ul>
  );
}

/**
 * The spine: one bar per shot across the video's runtime.
 *
 * The percentages are computed here, from `start_s`, `end_s` and the video's
 * own `duration_s` — all three are on the payload and none of the three is a
 * percentage (§20, "Percentages are not sent"). A shot can be two seconds of a
 * two-hour talk, so a bar has a minimum width in CSS: the bar's *position* is
 * the fact, and a mark too small to see is a mark that lies about the cut.
 *
 * Each bar is a real link carrying the `frame_offset` of the strip page that
 * holds its first keyframe — `ord` is dense per video, so the offset is
 * arithmetic rather than another query — plus `select`, which is the ordinal
 * the fragment carries, because a fragment never reaches a server and a bar
 * pointing off the current page has to navigate before it can mark anything.
 */
function Timeline({
  shots,
  capped,
  runtime,
  framePage,
  search,
  videoId,
  linked,
  onLink,
  onSelect,
}: {
  shots: Shot[];
  capped: boolean;
  runtime: number;
  framePage: number;
  search: string;
  videoId: string;
  linked: number | null;
  onLink: (shotId: number | null) => void;
  /** Mark the frame in place; `false` when it is not on this page of the
   *  strip, and the bar's own link is left to do the navigating. */
  onSelect: (ord: number) => boolean;
}) {
  const band = useRef<HTMLOListElement>(null);
  const scrub = useRef<HTMLDivElement>(null);
  // The shot the preview is on, and its picture. Both are refs rather than
  // state because they are read by the handler that decides whether anything
  // changed, and re-reading them must not be what re-runs it.
  const showing = useRef<number | null>(null);
  const settle = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const fetched = useRef(new Set<string>());
  const [preview, setPreview] = useState<{ shot: Shot; left: number; below: boolean } | null>(null);
  const [still, setStill] = useState<string | null>(null);

  // A native tooltip under a real preview is the same sentence told twice and
  // a second late. The bars carry it in the markup and it comes off here, on
  // bind — `dashboard.js:429-435` took it off at exactly this moment, and for
  // the same reason: this is the only place that knows the preview exists.
  // Taken off the node rather than not rendered, so the attribute is what the
  // markup holds for the paint before this runs; React re-sets it only when the
  // shots change, which is when this runs again.
  useEffect(() => {
    for (const bar of Array.from(band.current?.children ?? [])) bar.removeAttribute("title");
  }, [shots]);

  // A video with no recorded duration still has shots with ends: the band is
  // drawn against the furthest one rather than against zero.
  const span = runtime > 0 ? runtime : Math.max(...shots.map((s) => s.end_s), 1);

  // The bars' true geometry, from the percentages this page computed. Used
  // only when a hit test lands in a gap between bars: `min-width: 3px` means a
  // rendered bar can be wider than its share of the runtime, so where the two
  // disagree the pointer wins — you are pointing at a bar you can see.
  const geometry = shots.map((shot) => {
    const left = (100 * Math.min(shot.start_s, span)) / span;
    const width = (100 * Math.max(shot.end_s - shot.start_s, 0)) / span || 0.05;
    return { shot, left, width, right: left + width };
  });

  /** The bar under a pointer at `fraction` of the band, hit test first. */
  function barAt(target: EventTarget | null, fraction: number): Shot | null {
    const hit = target instanceof Element ? target.closest("[data-shot]") : null;
    const id = hit ? Number(hit.getAttribute("data-shot")) : NaN;
    const named = geometry.find((entry) => entry.shot.shot_id === id);
    if (named) return named.shot;

    const at = fraction * 100;
    let nearest: Shot | null = null;
    let distance = Infinity;
    for (const entry of geometry) {
      if (at >= entry.left && at <= entry.right) return entry.shot;
      const gap = at < entry.left ? entry.left - at : at - entry.right;
      if (gap < distance) {
        distance = gap;
        nearest = entry.shot;
      }
    }
    return nearest;
  }

  /**
   * Show the preview for one shot, with the pointer at `x` across the band.
   *
   * Horizontal: clamp the box inside the band, so a shot at either end is
   * previewed without the box hanging off the page. Vertical: above the band,
   * never over the bars it is describing — unless the band has been scrolled
   * near the top of the viewport and there is no room up there, in which case
   * it goes under. Measured, not guessed, which is why the box keeps its
   * layout while it is off rather than being taken out of the document.
   */
  function show(shot: Shot | null, x: number) {
    const bandBox = band.current?.getBoundingClientRect();
    const box = scrub.current;
    if (!shot || !bandBox || !box) return hide();

    const width = box.offsetWidth;
    const half = width / 2;
    const left =
      bandBox.width <= width
        ? bandBox.width / 2
        : Math.min(Math.max(x, half), bandBox.width - half);
    setPreview({ shot, left: Math.round(left), below: bandBox.top < box.offsetHeight + 16 });

    if (showing.current === shot.shot_id) return;
    showing.current = shot.shot_id;
    clearTimeout(settle.current);
    // A sweep across two hundred shots must not be two hundred requests, so a
    // frame not seen before waits out a short pause; one already asked for is
    // set immediately, because the cost of the second time is a cache lookup.
    // Either way the previous shot's frame is dropped rather than left under
    // the new shot's caption: an empty box is honest, a stale one is not.
    if (!shot.preview) return setStill(null);
    if (fetched.current.has(shot.preview)) return setStill(shot.preview);
    setStill(null);
    const url = shot.preview;
    settle.current = setTimeout(() => {
      fetched.current.add(url);
      setStill(url);
    }, SETTLE_MS);
  }

  function hide() {
    clearTimeout(settle.current);
    showing.current = null;
    setPreview(null);
    setStill(null);
  }

  if (!shots.length) {
    return (
      <Panel id="timeline" title="Scene timeline">
        <div className={styles.empty}>
          <p className={styles.emptyLead}>
            No keyframes were captured, so this video has no shots.
          </p>
          <p className={dash.emptyNote}>
            The <code>keyframe</code> row in Provenance says why.
          </p>
        </div>
      </Panel>
    );
  }

  return (
    <section className={styles.timeband} aria-labelledby="timeline">
      <h2 className={dash.srOnly} id="timeline">
        Scene timeline
      </h2>
      {/* Pointing along the band previews the shot under the pointer, and
          focus is the keyboard's pointer: tabbing and scrubbing put the same
          box in the same place. Arrow keys step shots by moving focus between
          the bars' own links, so whatever they land on, Enter follows. */}
      <ol
        aria-label="Shots across the runtime"
        className={styles.timeline}
        onBlur={(event) => {
          if (!band.current?.contains(event.relatedTarget)) hide();
        }}
        onFocus={(event) => {
          const bar = barElement(event.target);
          const bandBox = band.current?.getBoundingClientRect();
          if (!bar || !bandBox) return;
          const box = bar.getBoundingClientRect();
          show(barAt(bar, 0), box.left - bandBox.left + box.width / 2);
        }}
        onKeyDown={(event) => {
          if (event.altKey || event.ctrlKey || event.metaKey) return;
          if (event.key === "Escape") return hide();
          const bar = barElement(event.target);
          const bars = Array.from(band.current?.children ?? []);
          const index = bar ? bars.indexOf(bar) : -1;
          if (index < 0) return;
          const step = STEPS[event.key];
          const next =
            event.key === "Home"
              ? bars[0]
              : event.key === "End"
                ? bars[bars.length - 1]
                : step
                  ? bars[Math.min(Math.max(index + step, 0), bars.length - 1)]
                  : undefined;
          if (!next) return;
          event.preventDefault();
          next.querySelector("a")?.focus();
        }}
        onPointerLeave={hide}
        onPointerMove={(event) => {
          // A tap is a navigation, not a hover: on a touch screen the bar's own
          // link is the whole interaction and a preview would only be in front
          // of it.
          if (event.pointerType === "touch") return;
          const bandBox = band.current?.getBoundingClientRect();
          if (!bandBox?.width) return;
          const x = event.clientX - bandBox.left;
          show(barAt(event.target, x / bandBox.width), x);
        }}
        ref={band}
      >
        {geometry.map(({ shot, left, width }) => {
          const offset =
            Math.floor(shot.first_ord / Math.max(framePage, 1)) * Math.max(framePage, 1);
          const href = frameLink(search, videoId, offset, shot.first_ord);
          const label = `Shot ${shot.shot_id}, ${clock(shot.start_s)} to ${clock(shot.end_s)}, ${shot.kept} of ${shot.frames} keyframes kept`;
          return (
            <li
              className={`${styles.shotbar} ${shot.kept === 0 ? styles.dedup : ""} ${linked === shot.shot_id ? styles.isLinked : ""}`}
              data-shot={shot.shot_id}
              key={shot.shot_id}
              onBlur={() => onLink(null)}
              onFocus={() => onLink(shot.shot_id)}
              onPointerEnter={() => onLink(shot.shot_id)}
              onPointerLeave={() => onLink(null)}
              style={{ left: `${left}%`, width: `${width}%` }}
              // The bar's facts as a native tooltip, as Jinja had them on the
              // anchor — and only until the preview is bound, which is the
              // moment the platform's version of this sentence stops being the
              // best one on offer. On the `li` rather than on the anchor
              // because the anchor fills the bar.
              title={`shot ${shot.shot_id}, ${clock(shot.start_s)} to ${clock(shot.end_s)}, ${shot.kept}/${shot.frames} frames kept`}
            >
              <DashLink
                href={href}
                onClick={(event) => {
                  // A modified click is the reader asking for a second tab,
                  // and the whole point of the bar being a link is that they
                  // can.
                  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
                  if (onSelect(shot.first_ord)) event.preventDefault();
                }}
              >
                <span className={dash.srOnly}>{label}</span>
              </DashLink>
            </li>
          );
        })}
      </ol>
      {/* Empty of facts of its own and out of the a11y tree: every string it
          holds is already in the bar's own description, and a live region that
          repeats the thing you just focused is noise. */}
      <div
        aria-hidden="true"
        className={`${styles.scrubpreview} ${preview ? "" : styles.isOff} ${preview?.below ? styles.isBelow : ""}`}
        ref={scrub}
        style={{ left: `${preview?.left ?? 0}px` }}
      >
        <span className={styles.scrubshot}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          {still ? <img alt="" decoding="async" height={108} src={still} width={192} /> : null}
        </span>
        <span className={styles.scrubspan}>
          {preview ? `${clock(preview.shot.start_s)}–${clock(preview.shot.end_s)}` : ""}
        </span>
        <span className={styles.scrubmeta}>
          {preview
            ? `shot ${preview.shot.shot_id} · ${preview.shot.kept}/${preview.shot.frames} kept`
            : ""}
        </span>
      </div>
      {/* Quarter marks at their true percentage, not spaced by flexbox: the
          band above is an argument that a bar's position is a fact, and a scale
          whose labels are only approximately where they claim would undercut
          it. */}
      {/* The scale is the *video's* runtime quartered, which is what Jinja
          printed and what the header's `h:mm:ss` says. `span` is the band's
          fallback for a video with no recorded duration, and reading the ticks
          off it would put `1:44:12` under the last bar of a talk whose length
          nobody knows. */}
      <p className={styles.scale} aria-hidden="true">
        {[0, 25, 50, 75, 100].map((q) => (
          <span className={styles.tick} key={q} style={{ left: `${q}%` }}>
            {clock((runtime * q) / 100)}
          </span>
        ))}
      </p>
      <div className={styles.timebandFoot}>
        <p className={styles.panelNote}>
          {count(shots.length)} shot(s){capped ? ", capped: the video has more" : ""}.
        </p>
        {/* The hatch is the only mark on this page whose meaning is not written
            next to it, so it gets written here. Both swatches are the bars'
            own fills. */}
        <p className={styles.legend}>
          <span>
            <span className={`${styles.swatch} ${styles.swatchKept}`} aria-hidden="true" />
            keyframes kept
          </span>
          <span>
            <span className={`${styles.swatch} ${styles.swatchDedup}`} aria-hidden="true" />
            every frame deduplicated
          </span>
        </p>
      </div>
    </section>
  );
}

/**
 * The seven `video_stages` rows as they are. A stage with no row never ran,
 * which is a different fact from a stage that ran and produced nothing — so
 * the row stays, at the full seven, and recedes.
 *
 * The model column is drawn on both projections, as Jinja drew it. On the
 * public one `stages[].model_key` is `null` for every row (a declared model id
 * is a setting, §2.4) and every cell is a dash — which is the table keeping its
 * shape, five columns wide, so a reader comparing the demo against an owner's
 * screenshot is comparing two tables and not two layouts. Dropping the column
 * moved the two clocks a column left and made the redaction invisible: the
 * absence has to be somewhere you can see it. `error` is a different case and
 * still needs no column of its own — its row exists only when there is one.
 */
function Provenance({ stages }: { stages: Stage[] }) {
  return (
    <Panel id="provenance" title="Provenance">
      <div className={dash.tablewrap}>
        <table className={`${dash.grid} ${styles.stages}`}>
          <caption className={dash.srOnly}>
            Each pipeline stage, its state and the model that produced it
          </caption>
          <thead>
            <tr>
              <th scope="col">stage</th>
              <th scope="col">state</th>
              <th scope="col">model</th>
              <th scope="col">started</th>
              <th scope="col" className={dash.num}>
                took
              </th>
            </tr>
          </thead>
          <tbody>
            {stages.map((stage) => (
              <StageRows key={stage.stage} stage={stage} />
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function StageRows({ stage }: { stage: Stage }) {
  const tone =
    stage.state === "failed" ? styles.bad : stage.state === "absent" ? styles.absent : "";
  return (
    <>
      <tr className={tone}>
        <th scope="row">
          <code>{stage.stage}</code>
        </th>
        <td>
          <Pill state={stage.state} />
        </td>
        <td className={styles.colModel}>
          {/* `model_key` is NULL on every failed, skipped and invalidated
              stage — provenance records what *succeeded* — and on every row of
              the public projection. The page says "not recorded" rather than
              guessing, and rather than losing the column. */}
          {stage.model_key ? (
            <code>{stage.model_key}</code>
          ) : (
            <span className={styles.muted}>{DASH}</span>
          )}
        </td>
        <td>
          {stage.started_at ? (
            <time dateTime={iso(stage.started_at)}>{at(stage.started_at)}</time>
          ) : (
            <span className={styles.muted}>{DASH}</span>
          )}
        </td>
        <td className={dash.num}>{elapsed(stage.started_at, stage.finished_at)}</td>
      </tr>
      {stage.error ? (
        <tr className={styles.stageError}>
          <td colSpan={5}>
            <span className={styles.errLabel}>error</span>{" "}
            <span className={styles.errText}>{stage.error}</span>
          </td>
        </tr>
      ) : null}
    </>
  );
}

/**
 * One grid of every keyframe on this page, at the size the detection boxes
 * need to be read at. A frame that carries on-screen text draws its boxes over
 * the still and lists its lines beside it; one that carries none is the same
 * card without the list, with its `ocr_state` pill saying which kind of nothing
 * it is.
 *
 * The boxes are the coordinates as stored — normalised 0–1 at write time — so
 * drawing them costs nothing and is the single most convincing thing on the
 * page: it is the difference between "OCR ran" and "here is what it read, and
 * where". They are `style` attributes because each one is computed from its own
 * box, which is exactly what `style-src 'unsafe-inline'` in `proxy.ts` is for.
 *
 * Never inline base64: three widths of a `/frames/…` URL, and the card shows
 * the middle one.
 */
function Frames({
  frames,
  search,
  selected,
  videoId,
  linked,
  onLink,
  onOpen,
}: {
  frames: VideoDetail["frames"];
  search: string;
  selected: number | null;
  videoId: string;
  linked: number | null;
  onLink: (shotId: number | null) => void;
  onOpen: (frame: FrameCard) => void;
}) {
  return (
    <Panel id="frames" title="Frames, and what the machine read">
      {frames.frames.length ? (
        <>
          <ul className={styles.frames}>
            {frames.frames.map((frame) => (
              <Card
                key={frame.frame_id}
                frame={frame}
                linked={linked === frame.shot_id}
                onLink={onLink}
                onOpen={onOpen}
                selected={frame.ord === selected}
                videoId={videoId}
              />
            ))}
          </ul>
          {/* The one thing a per-frame count cannot say: the *page's* line
              budget ran out, so some card below lists fewer lines than its
              frame holds (§5.3's double cap). */}
          {frames.ocr_lines_capped ? (
            <p className={styles.panelNote}>
              The page&apos;s on-screen-text budget of{" "}
              <span className={dash.mono}>{count(frames.ocr_line_cap)}</span> lines is spent, so the
              last cards in this grid list fewer lines than they hold. Narrow the page with{" "}
              <code>?frames=</code> to read them.
            </p>
          ) : null}
          {frames.offset || frames.has_more ? (
            <nav className={styles.pager} aria-label="Keyframe pages">
              {frames.offset ? (
                <DashLink
                  className={styles.ghostlink}
                  href={frameLink(
                    search,
                    videoId,
                    Math.max(frames.offset - frames.limit, 0),
                    null,
                    "frames",
                  )}
                >
                  ← Earlier frames
                </DashLink>
              ) : null}
              {frames.has_more ? (
                <DashLink
                  className={styles.ghostlink}
                  href={frameLink(search, videoId, frames.offset + frames.limit, null, "frames")}
                >
                  Next {frames.limit} frames →
                </DashLink>
              ) : null}
            </nav>
          ) : null}
        </>
      ) : (
        <div className={styles.empty}>
          <p className={styles.emptyLead}>No keyframes on this page.</p>
          <p className={dash.emptyNote}>
            The <code>keyframes</code> figure above says how many exist in total.{" "}
            <code>skipped</code> on a card means deduplicated and never read; <code>empty</code>{" "}
            means read and blank.
          </p>
        </div>
      )}
    </Panel>
  );
}

function Card({
  frame,
  videoId,
  selected,
  linked,
  onLink,
  onOpen,
}: {
  frame: FrameCard;
  videoId: string;
  selected: boolean;
  linked: boolean;
  onLink: (shotId: number | null) => void;
  onOpen: (frame: FrameCard) => void;
}) {
  // Point at a line and its box lights. The pairing is by index, which is what
  // makes it hold for every line rather than for the handful a stylesheet
  // could enumerate as `:has()` pairs.
  //
  // Only that direction at this size: a detection box on a 512px still is a
  // few millimetres of screen, and a pointer aimed at one would be stealing
  // the click that opens the frame. The other half of the linkage — point at a
  // box, light its line — is the enlarged frame's, which is where a box is
  // something a pointer can find.
  const [lit, setLit] = useState<number | null>(null);

  return (
    <li
      className={`${styles.framecard} ${frame.dup_of_ord !== null ? styles.isDup : ""} ${selected ? styles.isSelected : ""} ${linked ? styles.isLinked : ""}`}
      id={`frame-${frame.ord}`}
      onBlur={() => onLink(null)}
      onFocus={() => onLink(frame.shot_id)}
      onPointerEnter={() => onLink(frame.shot_id)}
      onPointerLeave={() => onLink(null)}
    >
      {/* A button, not a link to the JPEG: clicking a frame opens it here, big
          enough to read the slide off, with its boxes and its lines beside it.
          The file itself stays one click further in, in the dialog's foot. */}
      <button className={styles.framebtn} onClick={() => onOpen(frame)} type="button">
        {/* A signed, expiring `/frames/…` URL on Python's origin, already sized
            by the API at the width it is displayed at. The optimizer would
            fetch and cache it past its own signature. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={frame.detail}
          alt={`Keyframe ${frame.ord} at ${clock(frame.t_s)}`}
          width={512}
          height={288}
          loading="lazy"
          decoding="async"
        />
        <OcrBoxes lines={frame.lines} lit={lit} />
      </button>
      <p className={styles.framemeta}>
        <a
          className={styles.at}
          href={`https://youtu.be/${videoId}?t=${Math.floor(frame.t_s)}`}
          rel="noopener noreferrer"
          target="_blank"
        >
          {clock(frame.t_s)}
        </a>
        <span className={styles.muted}>#{frame.ord}</span>
        <Pill state={frame.ocr_state} />
        {frame.lines.length ? (
          <span className={styles.muted}>{frame.lines.length} line(s)</span>
        ) : null}
      </p>
      <p className={`${styles.framemeta} ${styles.muted}`}>
        shot {frame.shot_id}
        <Sep />{" "}
        {frame.dup_of_ord !== null ? (
          <span className={styles.dupnote}>duplicate of #{frame.dup_of_ord}</span>
        ) : (
          <span>sharpness {frame.sharpness === null ? DASH : frame.sharpness.toFixed(1)}</span>
        )}
      </p>
      {frame.lines.length ? (
        <OcrLines className={styles.ocrlines} lines={frame.lines} lit={lit} onLit={setLit} />
      ) : null}
    </li>
  );
}

/**
 * The transcript, read from `/dashboard/api/videos/{id}/cues`.
 *
 * A pointer, not a copy (§20): the detail payload carries the totals and the
 * name and bounds of this endpoint, and the cues themselves arrive a page at a
 * time. Nearing the end of the box asks for the next batch and appends it,
 * which is what this scrollbox has done since 2026-08-10 — a click that
 * reloaded the page to move fifty rows threw the strip, the frames and the
 * reader's place away with it.
 *
 * **The place is still in the URL.** Appending in place is how a batch arrives;
 * it is not a reason for the position to stop being addressable. `?cue_offset=`
 * seeds the first read and names the first cue on screen, `?cues=` is the page
 * size the endpoint clamps, and the offset is written into the address bar with
 * `history.replaceState` under `#transcript` as the reader pages — so a link to
 * the fourth hour of a talk is a link somebody can send, exactly as it was in
 * Jinja, without the reload Jinja needed to honour it.
 *
 * The two buttons under the box are the same requests the scroll makes.
 * "Earlier" is the one the scroll cannot make: a seeded panel starts in the
 * middle of a transcript, and without it the rows before the seed are
 * unreachable without editing the URL by hand.
 *
 * The endpoint answers in numbers — `start_s`, `avg_logprob`, `chunk_opens` —
 * and every string it used to pre-render beside them went with the script that
 * read them (dashboard.md §23). The timecode, the confidence and the chunk
 * label are composed here.
 */
function Transcript({
  transcript,
  videoId,
  search,
  seedSize,
  seedOffset,
  onWhere,
}: {
  transcript: VideoDetail["transcript"];
  videoId: string;
  /** This page's own query, so the pager's two links can be the addresses
   *  Jinja's `nav_link` macro built rather than controls with no href. */
  search: string;
  /** `?cues=`, or `null` for the endpoint's own default. */
  seedSize: number | null;
  /** `?cue_offset=` — the first cue this panel asks for. */
  seedOffset: number;
  /** Where the reader is now and what the server ran, for the address bar. */
  onWhere: (offset: number, limit: number) => void;
}) {
  const [cues, setCues] = useState<Cue[]>([]);
  const [more, setMore] = useState(transcript.cues > 0);
  // `true` from the start, and set by whoever *asks* for a batch rather than
  // by the effect that fetches it: a `setState` in an effect body is a
  // cascading render, and the first batch is already in flight on mount.
  const [busy, setBusy] = useState(transcript.cues > 0);
  const [error, setError] = useState<unknown>(null);
  // Every batch asked for, in order; the effect fetches the last one. Pushing
  // to it is the whole of "load more" and "load earlier" — which direction the
  // batch goes is what the entry carries, because the response has to know
  // whether to append or to prepend.
  const [asks, setAsks] = useState<{ offset: number; back: boolean }[]>([
    { offset: seedOffset, back: false },
  ]);
  // The offset of the first cue on screen, and the offset of the next batch
  // forward. Both are refs, because they are written by the response and
  // re-reading them must not be what re-runs the effect that wrote them;
  // `first` is mirrored into state as well, because the Earlier control is
  // drawn from it and a ref does not re-render.
  const firstRef = useRef(seedOffset);
  const [first, setFirst] = useState(seedOffset);
  const nextOffset = useRef(seedOffset);
  // Set by "Earlier" and read once the batch it asked for has landed.
  const rewind = useRef(false);
  const box = useRef<HTMLDivElement>(null);

  const endpoint = transcript.endpoint;
  // The page size every request carries and every control counts in. `seedSize`
  // is already under the payload's own `max_limit`, so this is the number the
  // endpoint will run rather than one it would clamp under the reader.
  const size = seedSize ?? transcript.default_limit;
  const ask = asks[asks.length - 1];

  useEffect(() => {
    if (transcript.cues === 0) return;
    const controller = new AbortController();
    const query = new URLSearchParams({
      offset: String(ask.offset),
      limit: String(size),
    });
    dashboard.cues(endpoint, query, controller.signal).then(
      (page) => {
        if (controller.signal.aborted) return;
        // The server's own numbers, not this page's arithmetic: a short page is
        // where the list actually ends, and `page.offset` is where it started.
        if (ask.back) {
          // Only the rows that are actually earlier than what is on screen: a
          // backwards page that overlaps — the last one, against offset 0 —
          // would otherwise print its tail twice.
          const rows = page.cues.slice(0, Math.max(firstRef.current - page.offset, 0));
          firstRef.current = page.offset;
          setFirst(page.offset);
          setCues((shown) => [...rows, ...shown]);
        } else {
          nextOffset.current = page.offset + page.cues.length;
          setCues((shown) => [...shown, ...page.cues]);
          setMore(page.has_more);
        }
        // The address bar, every time a batch lands: where this view starts and
        // the page size it is being read at, so a `?cues=500` held under
        // `max_limit` stops claiming 500.
        onWhere(firstRef.current, size);
        setBusy(false);
      },
      (failure: unknown) => {
        if (controller.signal.aborted) return;
        // Stop asking and give the reader the button back. The batch already
        // on the page stays on it.
        setError(failure);
        setBusy(false);
      },
    );
    return () => controller.abort();
  }, [endpoint, size, ask, transcript.cues, onWhere]);

  // "Earlier" was a click asking to *see* what came before, so the box goes to
  // the top of the rows that just landed — which is what the Jinja pager's own
  // "← Earlier" navigation put on screen. Layout rather than an effect: it has
  // to happen before the paint that would otherwise show the old position.
  useLayoutEffect(() => {
    if (!rewind.current || !box.current) return;
    rewind.current = false;
    box.current.scrollTop = 0;
  }, [cues]);

  const loadMore = useCallback(() => {
    setError(null);
    setBusy(true);
    setAsks((held) => [...held, { offset: nextOffset.current, back: false }]);
  }, []);

  const loadEarlier = useCallback(() => {
    setError(null);
    setBusy(true);
    rewind.current = true;
    setAsks((held) => [...held, { offset: Math.max(firstRef.current - size, 0), back: true }]);
  }, [size]);

  // "Nearing the end" is one boxful short of it, which is the distance at
  // which the next batch has to already be arriving for the scroll not to stop.
  function onScroll() {
    const element = box.current;
    if (!element || busy || !more || error) return;
    if (element.scrollTop + element.clientHeight * 2 >= element.scrollHeight) loadMore();
  }

  // The empty *page*, which is what `video.html` keyed on — not the empty
  // transcript. `?cue_offset=` can land past the end of a transcript that does
  // exist, and what that reader met here was an empty scrollbox with a pager
  // under it rather than the panel that says so. The sentence has always been
  // page-scoped; now the condition is too.
  if (transcript.cues === 0 || (!busy && !error && cues.length === 0)) {
    return (
      <Panel id="transcript" title="Transcript">
        <div className={styles.empty}>
          {/* Page-scoped, as Jinja said it: "for this video" would be this page
              reporting an empty corpus row off an offset the reader typed. */}
          <p className={styles.emptyLead}>No transcript cues on this page.</p>
          <p className={dash.emptyNote}>
            The <code>stt</code> row in Provenance says whether one was produced.
          </p>
        </div>
      </Panel>
    );
  }

  return (
    <Panel id="transcript" title="Transcript">
      {/* Totals, not position (Tom, 2026-08-10): "cues 1–150 of 1,203" answered
          a question the scrollbar was already answering, and moved under the
          reader every time a batch landed. How big is this transcript is the
          question, and it has three answers. */}
      <p className={styles.cuepos}>
        <span className={dash.mono}>{count(transcript.cues)}</span> cues
        <Sep /> <span className={dash.mono}>{count(transcript.words)}</span> words
        <Sep /> <span className={dash.mono}>{count(transcript.chars)}</span> chars
      </p>
      <div className={styles.cuebox} onScroll={onScroll} ref={box} tabIndex={0}>
        <ol className={styles.cues}>
          {cues.map((cue, index) => (
            <CueRow key={`${cue.t}-${index}`} cue={cue} videoId={videoId} />
          ))}
        </ol>
        {busy ? <p className={styles.cueload}>loading</p> : null}
      </div>
      {error ? (
        <p className={styles.panelNote}>
          {error instanceof DashboardError ? error.message : "The next batch did not arrive."}
        </p>
      ) : null}
      {first > 0 || more ? (
        <nav className={styles.pager} aria-label="Transcript pages">
          {/* Real links, at the server's own offsets, exactly as `video.html`
              drew them: a reader can middle-click the next page of a transcript
              into a tab or copy its address, which a `<button>` does not offer
              and which is the whole difference between paging a document and
              operating a widget. The click is intercepted so the batch still
              arrives in place — the strip, the frames and the reader's position
              are not thrown away to move fifty rows — and a modified click is
              left to the browser, which is the point of the href.

              Only where there is something above the first row on screen —
              which on a panel nobody deep-linked into is never, and the control
              is not drawn at all. */}
          {first > 0 ? (
            <DashLink
              className={styles.ghostlink}
              href={cueLink(search, videoId, Math.max(first - size, 0))}
              onClick={(event) => {
                if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
                event.preventDefault();
                loadEarlier();
              }}
            >
              ← Earlier
            </DashLink>
          ) : null}
          {more ? (
            <DashLink
              className={styles.ghostlink}
              href={cueLink(search, videoId, first + cues.length)}
              onClick={(event) => {
                if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
                event.preventDefault();
                loadMore();
              }}
            >
              Next {size} cues →
            </DashLink>
          ) : null}
        </nav>
      ) : null}
    </Panel>
  );
}

function CueRow({ cue, videoId }: { cue: Cue; videoId: string }) {
  // The chunk label, composed from the chunk's own five fields — a value, not
  // policy text, so decision 5 puts it here.
  const opens = cue.chunk_opens;
  const mark = opens
    ? `chunk ${opens.seq} · ${clock(opens.start_s)}–${clock(opens.end_s)} · ${opens.n_words} words · ${opens.n_chars} chars`
    : null;
  const confidence = cue.avg_logprob !== null ? cue.avg_logprob.toFixed(2) : null;

  return (
    <>
      {mark ? (
        <li className={styles.chunkmark} aria-hidden="true">
          {mark}
        </li>
      ) : null}
      {/* The per-cue `origin` badge is deliberately gone: it printed `whisperx`
          on every one of a thousand rows to say what "What was stored" says
          once, per origin, with a count. */}
      <li className={`${styles.cue} ${cue.in_chunk ? styles.inChunk : ""}`}>
        {/* The timecode is the deeplink, as it was in Jinja and in the script
            that appended these rows: `t` is the whole second the payload
            carries for exactly this, so a line you have just read is one click
            from the moment it was said. Not `start_s` floored here — the
            endpoint sends the number the URL takes (§5.3). */}
        <a
          className={styles.at}
          href={`https://youtu.be/${encodeURIComponent(videoId)}?t=${cue.t}`}
          rel="noopener noreferrer"
          target="_blank"
        >
          {clock(cue.start_s)}
        </a>
        <span className={styles.cuetext}>{cue.text}</span>
        {cue.speaker ? <span className={styles.speaker}>{cue.speaker}</span> : null}
        {confidence ? (
          <span className={styles.conf} title="avg_logprob">
            {confidence}
          </span>
        ) : null}
      </li>
    </>
  );
}

function JobHistory({ history }: { history: VideoDetail["job_history"] }) {
  return (
    <Panel id="index-history" title="Recent indexing runs">
      {history.jobs.length ? (
        <>
          <div className={dash.tablewrap}>
            <table className={dash.grid}>
              <caption className={dash.srOnly}>The latest jobs that touched this video</caption>
              <thead>
                <tr>
                  <th scope="col">job</th>
                  <th scope="col">state</th>
                  <th scope="col">kind</th>
                  <th scope="col">created</th>
                  <th scope="col">finished</th>
                  <th scope="col">error</th>
                  <th scope="col">degraded stages</th>
                </tr>
              </thead>
              <tbody>
                {history.jobs.map((job) => (
                  <tr key={job.job_id}>
                    <th scope="row">
                      <DashLink href={`${ROOT}/jobs/${job.job_id}`}>
                        <code>{job.job_id}</code>
                      </DashLink>
                    </th>
                    <td>
                      <Pill state={job.state} />
                    </td>
                    <td>
                      <code>{job.kind}</code>
                    </td>
                    <td>{at(job.created_at)}</td>
                    <td>
                      {job.finished_at ? (
                        at(job.finished_at)
                      ) : (
                        <span className={styles.muted}>{DASH}</span>
                      )}
                    </td>
                    <td>
                      {job.error_code ? (
                        <code>{job.error_code}</code>
                      ) : (
                        <span className={styles.muted}>{DASH}</span>
                      )}
                    </td>
                    <td>
                      {job.degraded_stages.length ? (
                        job.degraded_stages.map((stage, index) => (
                          <span key={stage}>
                            <code>{stage}</code>
                            {index < job.degraded_stages.length - 1 ? ", " : ""}
                          </span>
                        ))
                      ) : (
                        <span className={styles.muted}>{DASH}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className={styles.panelNote}>
            Latest {count(history.cap)} at most; no total is computed.
          </p>
        </>
      ) : (
        <p className={dash.emptyNote}>No indexing job is linked to this video.</p>
      )}
    </Panel>
  );
}

/** How long a stage took, from the two clocks it stores.
 *
 *  The dash rather than a guess when either end is missing: a stage that
 *  failed has a `started_at` and no `finished_at`, and "running for 4 days"
 *  would be a sentence about a process that died (§4.1). */
export function elapsed(start: number | null, finish: number | null): string {
  if (!start || !finish || finish < start) return DASH;
  return duration(finish - start);
}

/** The named bounds this page's URL may hold, kept from `search`. */
function keptQuery(search: string, keys: string[]): URLSearchParams {
  const from = new URLSearchParams(search);
  const query = new URLSearchParams();
  for (const key of keys) {
    const value = from.get(key);
    if (value !== null && value.trim()) query.set(key, value.trim());
  }
  return query;
}

/** The strip's two parameters: what the *read* is bounded by, and no more. */
function frameQuery(search: string): URLSearchParams {
  return keptQuery(search, FRAME_KEYS);
}

/**
 * This page at another page of the strip, optionally marking one frame.
 *
 * Carries the transcript's two bounds through as well, exactly as Jinja's
 * `nav_link` macro did: paging the frames must not throw away where the reader
 * had got to in the transcript, and a link built from a page that was itself
 * reached by a `?cue_offset=` deep link has to stay that link.
 */
function frameLink(
  search: string,
  videoId: string,
  offset: number,
  select: number | null,
  anchor = "",
): string {
  const query = keptQuery(search, [...FRAME_KEYS, ...CUE_KEYS]);
  query.set("frame_offset", String(offset));
  if (select !== null) query.set("select", String(select));
  const fragment = anchor ? `#${anchor}` : select !== null ? `#frame-${select}` : "";
  return `${ROOT}/videos/${encodeURIComponent(videoId)}?${query}${fragment}`;
}

/**
 * This page at another page of the transcript, anchored at the panel.
 *
 * `nav_link(frame_offset, cue_offset, 'transcript')`, which is the address the
 * pager's two links have always carried: paging the transcript keeps the strip
 * where it is, and a page reached by a `?frame_offset=` deep link stays that
 * page.
 */
function cueLink(search: string, videoId: string, offset: number): string {
  const query = keptQuery(search, [...FRAME_KEYS, ...CUE_KEYS]);
  query.set("cue_offset", String(offset));
  return `${ROOT}/videos/${encodeURIComponent(videoId)}?${query}#transcript`;
}

/** `?cues=` and `?cue_offset=` as the panel's seed: a whole number, or nothing.
 *
 *  The ceiling is never invented here. `cues` is held under the payload's own
 *  `transcript.max_limit` and `cue_offset` under the endpoint's offset ceiling,
 *  and the real clamp is still the server's — it answers with the `limit` it
 *  ran and the panel pages by that number rather than by the typed one. A
 *  prompt-only bound is not a bound (CLAUDE.md); this one only keeps a URL
 *  someone hand-edited from asking for a page nobody could serve.
 *
 *  The floor is where the two keys differ. `cue_offset=0` is the top of the
 *  transcript and a real answer; `cues=0` is a page of no cues, which is a
 *  pager reading "Next 0 cues" and an Earlier that never moves. So a size is
 *  at least one cue, and a position is at least the first. */
function cueBound(raw: string | null, ceiling: number, floor: number): number | null {
  if (raw === null || !/^\d+$/.test(raw.trim())) return null;
  return Math.max(floor, Math.min(Number(raw.trim()), ceiling));
}

/** `?select=` as an ordinal, or `null`.
 *
 *  Not a clamp with a default, because every other bound on this page has a
 *  sensible one and this does not: defaulting to `0` would select frame 0 on
 *  every load, and a page that arrives with a keyframe already marked is a
 *  page reporting a click nobody made. */
function selectedOrd(raw: string | null): number | null {
  if (raw === null || !/^\d+$/.test(raw.trim())) return null;
  return Math.min(Number(raw.trim()), 100_000);
}
