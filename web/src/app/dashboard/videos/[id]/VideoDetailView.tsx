"use client";

import { useSearchParams } from "next/navigation";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type MouseEvent,
} from "react";
import { Pill } from "@/components/Pill";
import { dashboard, DashboardError, ROOT } from "@/lib/dashboard/client";
import { pick } from "@/lib/dashboard/query";
import { useResource } from "@/lib/dashboard/resource";
import type { Cue, FrameCard, Shot, Stage, VideoDetail } from "@/lib/dashboard/schemas";
import { at, bytes, clock, count, DASH, day, duration, hms, iso } from "@/lib/format";
import { FrameOverlay, OcrBoxes, OcrLines } from "../../FrameOverlay";
import controls from "../../kit/controls.module.css";
import { Notice, notice, ReadFailure, RefusalNotice } from "../../kit/notice";
import { Crumbs, Notes, Pager, table } from "../../kit/table";
import {
  DashLink,
  Fact,
  Figure,
  PageHead,
  Panel,
  Pending,
  Sep,
  StatePair,
  Title,
  ui,
  Unbroken,
} from "../../kit/ui";
import { useWriteSide } from "../../kit/write";
import { ReindexControl, TagsForm } from "../Manage";
import videos from "../videos.module.css";
import styles from "./detail.module.css";

// One video: what the pipeline did to it, what it produced, and what it read
// off the screen (dashboard.md §5.3, §20). The strip is bounded by
// `frames`/`frame_offset`; the transcript appends in place and keeps its place
// in `cues`/`cue_offset` with `replaceState`.

const FRAME_KEYS = ["frames", "frame_offset"];
const CUE_KEYS = ["cues", "cue_offset"];

/** The cue endpoint's own offset ceiling. */
const CUE_OFFSET_MAX = 500_000;

/** A new shot's still waits this long, so a sweep is not one request per bar. */
const SETTLE_MS = 70;

const STEPS: Record<string, number | undefined> = {
  ArrowRight: 1,
  ArrowLeft: -1,
  ArrowDown: 1,
  ArrowUp: -1,
};

function barElement(target: EventTarget | null): Element | null {
  return target instanceof Element ? target.closest("[data-shot]") : null;
}

/** Rewrite the address bar in place. The URL is the bookmark, not the state:
 *  a browser that refuses the rewrite loses a link and nothing on screen. */
function rewriteUrl(edit: (url: URL) => boolean | void): void {
  try {
    const here = new URL(window.location.href);
    if (edit(here) === false || here.href === window.location.href) return;
    window.history.replaceState(null, "", here.href);
  } catch {
    // Nothing lost.
  }
}

function markFrame(ord: number): void {
  rewriteUrl((url) => {
    url.searchParams.set("select", String(ord));
    url.hash = `frame-${ord}`;
  });
}

export function VideoDetailView({ videoId }: { videoId: string }) {
  const params = useSearchParams();
  const search = params.toString();
  // Only the strip's two bounds key the read: `select` marks a card already
  // on the page and must not re-read the video.
  const bounds = pick(params, FRAME_KEYS);
  const video = useResource(`video:${videoId}?${bounds}`, (signal) =>
    dashboard.video(videoId, bounds, signal),
  );
  const selected = selectedOrd(params.get("select"));

  if (video.data) {
    return (
      <Loaded
        data={video.data}
        search={search}
        selected={selected}
        seedSize={cueBound(params.get("cues"), video.data.transcript.max_limit, 1)}
        seedOffset={cueBound(params.get("cue_offset"), CUE_OFFSET_MAX, 0) ?? 0}
      />
    );
  }

  const refusal = video.error;
  if (refusal instanceof DashboardError && refusal.status === 404) {
    // There is no such video; a retry would say so again.
    return (
      <>
        <Title>Unknown video</Title>
        <Crumbs section="videos" label="Videos" id={videoId} />
        <PageHead title="Unknown video" />
        <RefusalNotice error={refusal} id="unknown" />
        <p className={notice.next}>
          <DashLink href={`${ROOT}/videos`}>Back to the videos table</DashLink>
        </p>
      </>
    );
  }
  return (
    <>
      <Crumbs section="videos" label="Videos" id={videoId} />
      <PageHead title="Video" />
      {refusal !== undefined ? <ReadFailure error={refusal} onRetry={video.reload} /> : <Pending />}
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
  // The row's tags after a write, read back; never a diff applied here.
  const [written, setWritten] = useState<string[] | null>(null);
  const tags = written ?? video.tags;
  // Open (in the lightbox) and selected (marked) are two facts: a bar marks a
  // frame without opening it.
  const [open, setOpen] = useState<FrameCard | null>(null);
  const [linked, setLinked] = useState<number | null>(null);
  const closeFrame = useCallback(() => setOpen(null), []);

  function openFrame(frame: FrameCard) {
    setOpen(frame);
    markFrame(frame.ord);
  }

  /** Mark a frame already on this strip page without navigating: scroll to it
   *  and focus its button, the control the next Enter opens. `false` when the
   *  card is on another page, and the bar's link does the navigating. */
  const selectFrame = useCallback((ord: number): boolean => {
    const card = document.getElementById(`frame-${ord}`);
    if (!card) return false;
    markFrame(ord);
    card.scrollIntoView({ block: "center", behavior: "smooth" });
    card.querySelector<HTMLButtonElement>("button")?.focus({ preventScroll: true });
    return true;
  }, []);

  // Neither key is introduced by paging: the ordinary address stays the one the
  // reader arrived at until they move off the top or typed a size.
  const onWhere = useCallback((offset: number, limit: number) => {
    rewriteUrl((url) => {
      const position = offset > 0 || url.searchParams.has("cue_offset");
      const sized = url.searchParams.has("cues");
      if (!position && !sized) return false;
      if (position) url.searchParams.set("cue_offset", String(offset));
      if (sized) url.searchParams.set("cues", String(limit));
      url.hash = "transcript";
    });
  }, []);

  return (
    <>
      <Title>{video.title}</Title>
      <Crumbs section="videos" label="Videos" id={video.video_id} />

      {/* Separators glue to the fact before them, with a real space after, so
          a strip keeps its break opportunities. */}
      <PageHead title={video.title}>
        <Unbroken>
          <StatePair label="index_state" word={video.index_state} />
        </Unbroken>{" "}
        {/* Both words, named, only when they differ (§4.5). */}
        {data.data_status && data.data_status !== video.index_state ? (
          <Unbroken>
            <StatePair label="data_status" word={data.data_status} />
          </Unbroken>
        ) : null}
      </PageHead>

      <p className={styles.facts}>
        {video.channel}
        <Sep /> <Fact label="published" value={day(video.published_at)} />
        <Sep /> {/* `h:mm:ss`: every other clock on the page is an offset into it. */}
        <Unbroken>
          <span className={ui.mono}>{hms(video.duration_s)}</span>
        </Unbroken>
        {video.language ? (
          <>
            <Sep />{" "}
            <Unbroken>
              <span className={ui.mono}>{video.language}</span>
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
                  className={ui.chip}
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

      {/* `video-summary`'s refusal: a fact about the video, not a failed read. */}
      {data.summary_error ? (
        <Notice
          id="summary-refused"
          title={data.summary_error.message}
          detail={<code>{data.summary_error.code}</code>}
          next={data.summary_error.next}
        />
      ) : null}

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
        <dl className={ui.figures}>
          <Figure
            label="cues"
            notes={[
              Object.keys(data.cue_origins).length ? (
                <>
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
                {/* The chapter's own start, not the payload's led `link`: a
                    boundary, not a quoted moment (§3.6). */}
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

      <FrameOverlay onClose={closeFrame} shot={open ? frameShot(open, video.video_id) : null} />
    </>
  );
}

/** One frame card as the overlay describes it; `lines`, even empty, asks for
 *  the OCR layer. */
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

/** A `GET` prefill into the index form, never a write. */
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

/** The write side, where registered. No delete: that job kind has no pipeline
 *  (§5.2). `id="manage"` is the videos table's Tag link target. */
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
    <section className={ui.panel} id="manage" aria-labelledby="manage-title">
      <h2 className={ui.panelTitle} id="manage-title">
        Manage this video
      </h2>

      <div className={ui.split}>
        <div className={videos.manageAction}>
          <h3 className={videos.label}>Re-index</h3>
          <p className={ui.emptyNote}>
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
          <p className={ui.emptyNote}>
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

/**
 * One bar per shot across the runtime. The percentages are computed here from
 * seconds on the payload (§20); a bar has a CSS minimum width because its
 * position is the fact. Each bar links to the strip page holding its first
 * keyframe, with `select` because a fragment never reaches a server.
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
  onSelect: (ord: number) => boolean;
}) {
  const band = useRef<HTMLOListElement>(null);
  const scrub = useRef<HTMLDivElement>(null);
  const showing = useRef<number | null>(null);
  const settle = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const fetched = useRef(new Set<string>());
  const [preview, setPreview] = useState<{ shot: Shot; left: number; below: boolean } | null>(null);
  const [still, setStill] = useState<string | null>(null);

  // A video with no recorded duration is drawn against its furthest shot.
  const span = runtime > 0 ? runtime : Math.max(...shots.map((s) => s.end_s), 1);

  const geometry = shots.map((shot) => {
    const left = (100 * Math.min(shot.start_s, span)) / span;
    const width = (100 * Math.max(shot.end_s - shot.start_s, 0)) / span || 0.05;
    return { shot, left, width, right: left + width };
  });

  /** The bar under the pointer; in a gap between bars (the CSS minimum width
   *  makes rendered bars wider than their share), the nearest one. */
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

  /** Clamp the preview inside the band, above the bars unless there is no room
   *  above them in the viewport. */
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
    // A stale still under a new caption is worse than an empty box.
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
          <p className={ui.emptyNote}>
            The <code>keyframe</code> row in Provenance says why.
          </p>
        </div>
      </Panel>
    );
  }

  return (
    <section className={styles.timeband} aria-labelledby="timeline">
      <h2 className={ui.srOnly} id="timeline">
        Scene timeline
      </h2>
      {/* Focus is the keyboard's pointer; arrows move focus between the bars'
          own links, so Enter always follows one. */}
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
          // On touch the bar's link is the whole interaction.
          if (event.pointerType === "touch") return;
          const bandBox = band.current?.getBoundingClientRect();
          if (!bandBox?.width) return;
          const x = event.clientX - bandBox.left;
          show(barAt(event.target, x / bandBox.width), x);
        }}
        ref={band}
      >
        {geometry.map(({ shot, left, width }) => {
          const page = Math.max(framePage, 1);
          const offset = Math.floor(shot.first_ord / page) * page;
          const href = frameLink(search, videoId, offset, shot.first_ord);
          const label = `Shot ${shot.shot_id}, ${clock(shot.start_s)} to ${clock(shot.end_s)}, ${shot.kept} of ${shot.frames} keyframes kept`;
          const isLinked = linked === shot.shot_id;
          return (
            <li
              className={`${styles.shotbar} ${shot.kept === 0 ? styles.dedup : ""} ${isLinked ? styles.isLinked : ""}`}
              data-linked={isLinked || undefined}
              data-shot={shot.shot_id}
              key={shot.shot_id}
              onBlur={() => onLink(null)}
              onFocus={() => onLink(shot.shot_id)}
              onPointerEnter={() => onLink(shot.shot_id)}
              onPointerLeave={() => onLink(null)}
              style={{ left: `${left}%`, width: `${width}%` }}
            >
              <DashLink
                href={href}
                onClick={(event) => {
                  // A modified click asks for a new tab.
                  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
                  if (onSelect(shot.first_ord)) event.preventDefault();
                }}
              >
                <span className={ui.srOnly}>{label}</span>
              </DashLink>
            </li>
          );
        })}
      </ol>
      {/* Out of the a11y tree: every string is already in the bar's label. */}
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
      {/* The video's runtime quartered, at true percentages. */}
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
 * The seven stages as they are; `absent` never ran and recedes. The model
 * column stays on the projection, where every cell is a dash, so the redaction
 * is visible and the table keeps its shape (§2.4).
 */
function Provenance({ stages }: { stages: Stage[] }) {
  return (
    <Panel id="provenance" title="Provenance">
      <div className={table.tablewrap}>
        <table className={`${table.grid} ${styles.stages}`}>
          <caption className={ui.srOnly}>
            Each pipeline stage, its state and the model that produced it
          </caption>
          <thead>
            <tr>
              <th scope="col">stage</th>
              <th scope="col">state</th>
              <th scope="col">model</th>
              <th scope="col">started</th>
              <th scope="col" className={table.num}>
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
        {/* `model_key` records what succeeded, and is `null` in the projection. */}
        <td className={styles.colModel}>
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
        <td className={table.num}>{elapsed(stage.started_at, stage.finished_at)}</td>
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
 * Every keyframe on this strip page with its detection boxes drawn at the
 * stored 0–1 coordinates (inline `style`, which the CSP allows) and its lines
 * beside it. Frames by URL, never inline base64.
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
          {/* The page's line budget ran out: §5.3's double cap. */}
          {frames.ocr_lines_capped ? (
            <p className={styles.panelNote}>
              The page&apos;s on-screen-text budget of{" "}
              <span className={ui.mono}>{count(frames.ocr_line_cap)}</span> lines is spent, so the
              last cards in this grid list fewer lines than they hold. Narrow the page with{" "}
              <code>?frames=</code> to read them.
            </p>
          ) : null}
          <Pager
            limit={frames.limit}
            offset={frames.offset}
            hasMore={frames.has_more}
            href={(offset) => frameLink(search, videoId, offset, null, "frames")}
            previous="← Earlier frames"
            next={`Next ${frames.limit} frames →`}
            label="Keyframe pages"
          />
        </>
      ) : (
        <div className={styles.empty}>
          <p className={styles.emptyLead}>No keyframes on this page.</p>
          <p className={ui.emptyNote}>
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
  // Line → box only at card size: a box on a 512px still is not a target.
  const [lit, setLit] = useState<number | null>(null);

  return (
    <li
      className={`${styles.framecard} ${frame.dup_of_ord !== null ? styles.isDup : ""} ${selected ? styles.isSelected : ""} ${linked ? styles.isLinked : ""}`}
      data-linked={linked || undefined}
      data-selected={selected || undefined}
      id={`frame-${frame.ord}`}
      onBlur={() => onLink(null)}
      onFocus={() => onLink(frame.shot_id)}
      onPointerEnter={() => onLink(frame.shot_id)}
      onPointerLeave={() => onLink(null)}
    >
      <button className={styles.framebtn} onClick={() => onOpen(frame)} type="button">
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
 * The transcript, a batch at a time from the endpoint the payload names (§20).
 * Nearing the end of the box appends the next batch; Earlier prepends, for a
 * panel seeded mid-transcript. The place is written back to the URL
 * (`onWhere`), so a position is a link somebody can send.
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
  search: string;
  /** `?cues=`, or `null` for the endpoint's default. */
  seedSize: number | null;
  /** `?cue_offset=`, the first cue asked for. */
  seedOffset: number;
  onWhere: (offset: number, limit: number) => void;
}) {
  const [cues, setCues] = useState<Cue[]>([]);
  const [more, setMore] = useState(transcript.cues > 0);
  // Set by whoever asks for a batch, not by the effect that fetches it.
  const [busy, setBusy] = useState(transcript.cues > 0);
  const [error, setError] = useState<unknown>(null);
  // Every batch asked for; the effect fetches the last one.
  const [asks, setAsks] = useState<{ offset: number; back: boolean }[]>([
    { offset: seedOffset, back: false },
  ]);
  const firstRef = useRef(seedOffset);
  const [first, setFirst] = useState(seedOffset);
  const nextOffset = useRef(seedOffset);
  const rewind = useRef(false);
  const box = useRef<HTMLDivElement>(null);

  const endpoint = transcript.endpoint;
  const size = seedSize ?? transcript.default_limit;
  const ask = asks[asks.length - 1];

  useEffect(() => {
    if (transcript.cues === 0) return;
    const controller = new AbortController();
    const query = new URLSearchParams({ offset: String(ask.offset), limit: String(size) });
    dashboard.cues(endpoint, query, controller.signal).then(
      (page) => {
        if (controller.signal.aborted) return;
        if (ask.back) {
          // Only rows earlier than what is on screen: the first page overlaps.
          const rows = page.cues.slice(0, Math.max(firstRef.current - page.offset, 0));
          firstRef.current = page.offset;
          setFirst(page.offset);
          setCues((shown) => [...rows, ...shown]);
        } else {
          nextOffset.current = page.offset + page.cues.length;
          setCues((shown) => [...shown, ...page.cues]);
          setMore(page.has_more);
        }
        onWhere(firstRef.current, size);
        setBusy(false);
      },
      (failure: unknown) => {
        if (controller.signal.aborted) return;
        setError(failure);
        setBusy(false);
      },
    );
    return () => controller.abort();
  }, [endpoint, size, ask, transcript.cues, onWhere]);

  // Earlier asked to see what came before, so the box goes to its top before
  // the next paint.
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

  // One boxful short of the end, so the next batch is arriving before the
  // scroll would stop.
  function onScroll() {
    const element = box.current;
    if (!element || busy || !more || error) return;
    if (element.scrollTop + element.clientHeight * 2 >= element.scrollHeight) loadMore();
  }

  // The empty page, not the empty transcript: `?cue_offset=` can land past the
  // end of one that exists.
  if (transcript.cues === 0 || (!busy && !error && cues.length === 0)) {
    return (
      <Panel id="transcript" title="Transcript">
        <div className={styles.empty}>
          <p className={styles.emptyLead}>No transcript cues on this page.</p>
          <p className={ui.emptyNote}>
            The <code>stt</code> row in Provenance says whether one was produced.
          </p>
        </div>
      </Panel>
    );
  }

  const plain = (event: MouseEvent) =>
    !(event.metaKey || event.ctrlKey || event.shiftKey || event.altKey);
  // Until the first batch lands the box holds the rows it is about to show.
  const reserve =
    busy && cues.length === 0
      ? ({ "--cue-rows": Math.min(size, transcript.cues) } as CSSProperties)
      : undefined;

  return (
    <Panel id="transcript" title="Transcript">
      {/* Totals, not position: the scrollbar already says where you are. */}
      <p className={styles.cuepos}>
        <span className={ui.mono}>{count(transcript.cues)}</span> cues
        <Sep /> <span className={ui.mono}>{count(transcript.words)}</span> words
        <Sep /> <span className={ui.mono}>{count(transcript.chars)}</span> chars
      </p>
      <div
        className={`${styles.cuebox} ${reserve ? styles.cueboxPending : ""}`}
        onScroll={onScroll}
        ref={box}
        style={reserve}
        tabIndex={0}
      >
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
      {/* Real links at the server's offsets, so a page can go to a new tab; a
          plain click appends in place instead. */}
      {first > 0 || more ? (
        <nav className={table.pager} aria-label="Transcript pages">
          {first > 0 ? (
            <DashLink
              className={controls.ghostlink}
              href={cueLink(search, videoId, Math.max(first - size, 0))}
              onClick={(event) => {
                if (!plain(event)) return;
                event.preventDefault();
                loadEarlier();
              }}
            >
              ← Earlier
            </DashLink>
          ) : null}
          {more ? (
            <DashLink
              className={controls.ghostlink}
              href={cueLink(search, videoId, first + cues.length)}
              onClick={(event) => {
                if (!plain(event)) return;
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
      <li className={`${styles.cue} ${cue.in_chunk ? styles.inChunk : ""}`}>
        {/* `t` is the whole second the endpoint sends for the deeplink. */}
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
          <div className={table.tablewrap}>
            <table className={table.grid}>
              <caption className={ui.srOnly}>The latest jobs that touched this video</caption>
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
        <p className={ui.emptyNote}>No indexing job is linked to this video.</p>
      )}
    </Panel>
  );
}

/** How long a stage took, or the dash when either clock is missing: a failed
 *  stage has a start and no finish (§4.1). */
export function elapsed(start: number | null, finish: number | null): string {
  if (!start || !finish || finish < start) return DASH;
  return duration(finish - start);
}

/** This page at another strip page, optionally marking a frame; the
 *  transcript's two bounds ride along so paging frames keeps its place. */
function frameLink(
  search: string,
  videoId: string,
  offset: number,
  select: number | null,
  anchor = "",
): string {
  const query = pick(search, [...FRAME_KEYS, ...CUE_KEYS]);
  query.set("frame_offset", String(offset));
  if (select !== null) query.set("select", String(select));
  const fragment = anchor ? `#${anchor}` : select !== null ? `#frame-${select}` : "";
  return `${ROOT}/videos/${encodeURIComponent(videoId)}?${query}${fragment}`;
}

/** This page at another transcript page; the strip stays where it is. */
function cueLink(search: string, videoId: string, offset: number): string {
  const query = pick(search, [...FRAME_KEYS, ...CUE_KEYS]);
  query.set("cue_offset", String(offset));
  return `${ROOT}/videos/${encodeURIComponent(videoId)}?${query}#transcript`;
}

/** A whole number between `floor` and `ceiling`, or `null`. A size is at least
 *  one cue (`cues=0` would page by nothing); a position may be the top. The
 *  real clamp is still the server's. */
function cueBound(raw: string | null, ceiling: number, floor: number): number | null {
  if (raw === null || !/^\d+$/.test(raw.trim())) return null;
  return Math.max(floor, Math.min(Number(raw.trim()), ceiling));
}

/** `?select=` as an ordinal, or `null`: no default, since frame 0 marked on
 *  every load would report a click nobody made. */
function selectedOrd(raw: string | null): number | null {
  if (raw === null || !/^\d+$/.test(raw.trim())) return null;
  return Math.min(Number(raw.trim()), 100_000);
}
