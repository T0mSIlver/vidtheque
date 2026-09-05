"use client";

import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { Pill } from "@/components/Pill";
import { dashboard, DashboardError, ROOT } from "@/lib/dashboard/client";
import type { Cue, FrameCard, Shot, Stage, VideoDetail } from "@/lib/dashboard/schemas";
import { at, bytes, clock, count, DASH, day, duration, iso } from "@/lib/format";
import dash from "../../dashboard.module.css";
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
} from "../../parts";
import { useRead } from "../../useRead";
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
// Two bounds are the URL's, `frames` and `frame_offset`, exactly as they were
// in Jinja. The transcript's are not: the panel appends its next batch in
// place rather than reloading the page, so where the reader is in the
// transcript is not a fact about the page and has no business in a link
// somebody sends. Both are still Python's numbers — `?frames=200` is clamped
// to 96 server-side and the payload's `notes` says so.

const FRAME_KEYS = ["frames", "frame_offset"];

export function VideoDetailView({ videoId }: { videoId: string }) {
  const params = useSearchParams();
  const search = params.toString();
  const read = useCallback(
    (signal: AbortSignal) => dashboard.video(videoId, frameQuery(search), signal),
    [videoId, search],
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
      return (
        <>
          <Crumbs videoId={videoId} />
          <PageHead title="Unknown video" />
          <section className={dash.notice} aria-labelledby="unknown">
            <h2 className={dash.noticeTitle} id="unknown">
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
    return (
      <>
        <Crumbs videoId={videoId} />
        <PageHead title="Video" />
        <ReadFailure error={state.error} onRetry={state.reload} />
      </>
    );
  }

  return <Loaded data={state.data} search={search} selected={selected} />;
}

function Loaded({
  data,
  search,
  selected,
}: {
  data: VideoDetail;
  search: string;
  selected: number | null;
}) {
  const { video, counts, frames, shots } = data;
  const runtime = video.duration_s ?? 0;
  const failed = data.stages.filter((stage) => stage.state === "failed");

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
        <Unbroken>
          <span className={dash.mono}>{duration(video.duration_s)}</span>
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
        {video.tags.length ? (
          <>
            <Sep />{" "}
            <span className={styles.taglist}>
              {video.tags.map((tag) => (
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
      />

      <Panel id="counts" title="What was stored">
        <dl className={dash.figures}>
          <Figure
            label="cues"
            notes={[
              Object.keys(data.cue_origins).length ? (
                <>
                  {Object.entries(data.cue_origins).map(([origin, n], index) => (
                    <span key={origin}>
                      {index ? " · " : ""}
                      {origin} {count(n)}
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

      <Provenance stages={data.stages} redacted={data.redacted} />

      <Frames frames={frames} search={search} selected={selected} videoId={video.video_id} />

      <Transcript key={video.video_id} transcript={data.transcript} />

      {data.chapters.length ? (
        <Panel id="chapters" title="Chapters">
          <ol className={styles.chapters}>
            {data.chapters.map((chapter) => (
              <li className={styles.chapter} key={`${chapter.start_s}-${chapter.title}`}>
                <a
                  className={styles.at}
                  href={chapter.link ?? video.url}
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
    </>
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
}: {
  shots: Shot[];
  capped: boolean;
  runtime: number;
  framePage: number;
  search: string;
  videoId: string;
}) {
  // A video with no recorded duration still has shots with ends: the band is
  // drawn against the furthest one rather than against zero.
  const span = runtime > 0 ? runtime : Math.max(...shots.map((s) => s.end_s), 1);

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
      <ol className={styles.timeline} aria-label="Shots across the runtime">
        {shots.map((shot) => {
          const left = (100 * Math.min(shot.start_s, span)) / span;
          const width = (100 * Math.max(shot.end_s - shot.start_s, 0)) / span || 0.05;
          const offset =
            Math.floor(shot.first_ord / Math.max(framePage, 1)) * Math.max(framePage, 1);
          const href = frameLink(search, videoId, offset, shot.first_ord);
          const label = `Shot ${shot.shot_id}, ${clock(shot.start_s)} to ${clock(shot.end_s)}, ${shot.kept} of ${shot.frames} keyframes kept`;
          return (
            <li
              className={`${styles.shotbar} ${shot.kept === 0 ? styles.dedup : ""}`}
              key={shot.shot_id}
              style={{ left: `${left}%`, width: `${width}%` }}
            >
              <DashLink href={href}>
                <span className={dash.srOnly}>{label}</span>
              </DashLink>
            </li>
          );
        })}
      </ol>
      {/* Quarter marks at their true percentage, not spaced by flexbox: the
          band above is an argument that a bar's position is a fact, and a scale
          whose labels are only approximately where they claim would undercut
          it. */}
      <p className={styles.scale} aria-hidden="true">
        {[0, 25, 50, 75, 100].map((q) => (
          <span className={styles.tick} key={q} style={{ left: `${q}%` }}>
            {clock((span * q) / 100)}
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
 * The model column is the projection's designed absence. `stages[].model_key`
 * is `null` there for every row (a declared model id is a setting, §2.4), and
 * a column of seven dashes would be the page reporting "not recorded" about
 * seven stages that recorded it — so the column is not drawn at all, exactly
 * as the overview's declared-models table is simply absent. `error` goes the
 * same way and needs no column: its row only exists when there is one.
 */
function Provenance({ stages, redacted }: { stages: Stage[]; redacted: boolean }) {
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
              {redacted ? null : <th scope="col">model</th>}
              <th scope="col">started</th>
              <th scope="col" className={dash.num}>
                took
              </th>
            </tr>
          </thead>
          <tbody>
            {stages.map((stage) => (
              <StageRows key={stage.stage} stage={stage} columns={redacted ? 4 : 5} />
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function StageRows({ stage, columns }: { stage: Stage; columns: number }) {
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
        {columns === 5 ? (
          <td className={styles.colModel}>
            {/* `model_key` is NULL on every failed, skipped and invalidated
                stage: provenance records what *succeeded*, and the page says
                "not recorded" rather than guessing. */}
            {stage.model_key ? (
              <code>{stage.model_key}</code>
            ) : (
              <span className={styles.muted}>{DASH}</span>
            )}
          </td>
        ) : null}
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
          <td colSpan={columns}>
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
}: {
  frames: VideoDetail["frames"];
  search: string;
  selected: number | null;
  videoId: string;
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
                videoId={videoId}
                selected={frame.ord === selected}
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
}: {
  frame: FrameCard;
  videoId: string;
  selected: boolean;
}) {
  // Point at a line, light its box; point at a box, light its line. The
  // pairing is by index, which is what makes it hold for every line rather
  // than for the handful a stylesheet could enumerate as `:has()` pairs.
  const [lit, setLit] = useState<number | null>(null);

  return (
    <li
      className={`${styles.framecard} ${frame.dup_of_ord !== null ? styles.isDup : ""} ${selected ? styles.isSelected : ""}`}
      id={`frame-${frame.ord}`}
    >
      <a
        className={styles.framebtn}
        href={frame.large}
        rel="noopener noreferrer"
        target="_blank"
        title={`${frame.frame_id} · ${clock(frame.t_s)} · ${frame.width}×${frame.height} · ${bytes(frame.jpeg_bytes)}`}
      >
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
        {frame.lines.map((line, index) => (
          <span
            className={`${styles.ocrbox} ${lit === index ? styles.isLit : ""}`}
            aria-hidden="true"
            key={line.line_no}
            style={{
              left: `${line.box[0] * 100}%`,
              top: `${line.box[1] * 100}%`,
              width: `${(line.box[2] - line.box[0]) * 100}%`,
              height: `${(line.box[3] - line.box[1]) * 100}%`,
            }}
          />
        ))}
      </a>
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
        <ol className={styles.ocrlines}>
          {frame.lines.map((line, index) => (
            <li
              className={`${styles.ocrline} ${lit === index ? styles.isLit : ""}`}
              key={line.line_no}
              onMouseEnter={() => setLit(index)}
              onMouseLeave={() => setLit(null)}
              onFocus={() => setLit(index)}
              onBlur={() => setLit(null)}
              tabIndex={0}
            >
              <span className={styles.ocrtext}>{line.text}</span>
              {line.conf !== null ? (
                <span className={styles.conf}>{line.conf.toFixed(2)}</span>
              ) : null}
            </li>
          ))}
        </ol>
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
 * which is what the Jinja scrollbox has done since 2026-08-10 — a click that
 * reloaded the page to move fifty rows threw the strip, the frames and the
 * reader's place away with it.
 *
 * The button under the box is the same request the scroll makes. It is not a
 * second way to page: it is the keyboard's way, and the one control left when
 * a fetch fails.
 *
 * **The typed fields are preferred and the strings are the fallback.** The
 * endpoint answers with both halves — `start_s`/`avg_logprob`/`chunk_opens`
 * beside `at`/`conf`/`chunk` — so this renders from the numbers and falls back
 * to the rendered strings on an instance that predates them. When the strings
 * are cut, the three `??` below go and `schemas.ts` loses three fields.
 */
function Transcript({ transcript }: { transcript: VideoDetail["transcript"] }) {
  const [cues, setCues] = useState<Cue[]>([]);
  const [more, setMore] = useState(transcript.cues > 0);
  // `true` from the start, and set by whoever *asks* for a batch rather than
  // by the effect that fetches it: a `setState` in an effect body is a
  // cascading render, and the first batch is already in flight on mount.
  const [busy, setBusy] = useState(transcript.cues > 0);
  const [error, setError] = useState<unknown>(null);
  // How many batches have been asked for. Bumping it is the whole of "load
  // more"; the offset itself is a ref, because it is written by the response
  // and re-reading it must not be what re-runs the effect.
  const [wanted, setWanted] = useState(1);
  const nextOffset = useRef(0);
  const box = useRef<HTMLDivElement>(null);

  const endpoint = transcript.endpoint;
  const size = transcript.default_limit;

  useEffect(() => {
    if (transcript.cues === 0) return;
    const controller = new AbortController();
    const query = new URLSearchParams({
      offset: String(nextOffset.current),
      limit: String(size),
    });
    dashboard.cues(endpoint, query, controller.signal).then(
      (page) => {
        if (controller.signal.aborted) return;
        // The server's own numbers, not this page's arithmetic: `limit` is
        // clamped server-side and a short page is where the list actually ends.
        nextOffset.current = page.offset + page.cues.length;
        setCues((rows) => [...rows, ...page.cues]);
        setMore(page.has_more);
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
  }, [endpoint, size, wanted, transcript.cues]);

  const loadMore = useCallback(() => {
    setError(null);
    setBusy(true);
    setWanted((n) => n + 1);
  }, []);

  // "Nearing the end" is one boxful short of it, which is the distance at
  // which the next batch has to already be arriving for the scroll not to stop.
  function onScroll() {
    const element = box.current;
    if (!element || busy || !more || error) return;
    if (element.scrollTop + element.clientHeight * 2 >= element.scrollHeight) loadMore();
  }

  if (transcript.cues === 0) {
    return (
      <Panel id="transcript" title="Transcript">
        <div className={styles.empty}>
          <p className={styles.emptyLead}>No transcript cues for this video.</p>
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
            <CueRow key={`${cue.t}-${index}`} cue={cue} />
          ))}
        </ol>
        {busy ? <p className={styles.cueload}>loading</p> : null}
      </div>
      {error ? (
        <p className={styles.panelNote}>
          {error instanceof DashboardError ? error.message : "The next batch did not arrive."}
        </p>
      ) : null}
      {more ? (
        <nav className={styles.pager} aria-label="Transcript pages">
          <button className={styles.ghostlink} type="button" onClick={loadMore} disabled={busy}>
            Next {size} cues →
          </button>
        </nav>
      ) : null}
    </Panel>
  );
}

function CueRow({ cue }: { cue: Cue }) {
  // The chunk label, composed from the chunk's own five fields — a value, not
  // policy text, so decision 5 puts it here. `cue.chunk` is the endpoint's
  // pre-rendered copy of this sentence and is the fallback until the strings
  // are cut.
  const opens = cue.chunk_opens;
  const mark = opens
    ? `chunk ${opens.seq} · ${clock(opens.start_s)}–${clock(opens.end_s)} · ${opens.n_words} words · ${opens.n_chars} chars`
    : cue.chunk;
  const confidence =
    cue.avg_logprob !== undefined && cue.avg_logprob !== null
      ? cue.avg_logprob.toFixed(2)
      : cue.conf;

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
        <span className={styles.at}>{cue.start_s !== undefined ? clock(cue.start_s) : cue.at}</span>
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

/** The strip's two parameters, and nothing else this page's URL may hold. */
function frameQuery(search: string): URLSearchParams {
  const from = new URLSearchParams(search);
  const query = new URLSearchParams();
  for (const key of FRAME_KEYS) {
    const value = from.get(key);
    if (value !== null && value.trim()) query.set(key, value.trim());
  }
  return query;
}

/** This page at another page of the strip, optionally marking one frame. */
function frameLink(
  search: string,
  videoId: string,
  offset: number,
  select: number | null,
  anchor = "",
): string {
  const query = frameQuery(search);
  query.set("frame_offset", String(offset));
  if (select !== null) query.set("select", String(select));
  const fragment = anchor ? `#${anchor}` : select !== null ? `#frame-${select}` : "";
  return `${ROOT}/videos/${encodeURIComponent(videoId)}?${query}${fragment}`;
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
