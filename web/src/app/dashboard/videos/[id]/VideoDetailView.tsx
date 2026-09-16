"use client";

import { useSearchParams } from "next/navigation";
import { useCallback, useRef, useState } from "react";
import { dashboard, DashboardError, ROOT } from "@/lib/dashboard/client";
import { pick } from "@/lib/dashboard/query";
import { useResource } from "@/lib/dashboard/resource";
import type { FrameCard, VideoDetail } from "@/lib/dashboard/schemas";
import { clock } from "@/lib/format";
import { FrameOverlay } from "@/components/dashboard/FrameOverlay";
import { notice, ReadFailure, RefusalNotice } from "@/components/dashboard/kit/notice";
import { Crumbs } from "@/components/dashboard/kit/table";
import { DashLink, PageHead, Panel, Pending, Title } from "@/components/dashboard/kit/ui";
import { ManagePanel } from "../Manage";
import styles from "./detail.module.css";
import { Frames, frameShot } from "./Frames";
import { Head } from "./Head";
import { JobHistory } from "./JobHistory";
import { linkShot, shotOf } from "./link";
import { Provenance, Stored } from "./Provenance";
import { CUE_OFFSET_MAX, cueBound, FRAME_KEYS, markFrame, selectedOrd } from "./query";
import { Timeline } from "./Timeline";
import { Transcript } from "./Transcript";

// One video: what the pipeline did to it, what it produced, and what it read
// off the screen (dashboard.md §5.3, §20).

export function VideoDetailView({ videoId }: { videoId: string }) {
  const params = useSearchParams();
  // Only the strip's two bounds key the read: `select` and the transcript's
  // place are rewritten in place and must not re-read the video.
  const bounds = pick(params, FRAME_KEYS);
  const video = useResource(`video:${videoId}?${bounds}`, (signal) =>
    dashboard.video(videoId, bounds, signal),
  );
  // Another strip page keeps this one on screen until it lands.
  const [held, setHeld] = useState(video.data);
  if (video.data !== undefined && video.data !== held) setHeld(video.data);
  const data = video.data ?? held;

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

  if (data) {
    return (
      <Loaded
        data={data}
        search={params.toString()}
        selected={selectedOrd(params.get("select"))}
        paging={video.data === undefined}
        pagingError={video.data === undefined ? refusal : undefined}
        onRetry={video.reload}
        seedSize={cueBound(params.get("cues"), data.transcript.max_limit, 1)}
        seedOffset={cueBound(params.get("cue_offset"), CUE_OFFSET_MAX, 0) ?? 0}
      />
    );
  }

  return (
    <>
      <Crumbs section="videos" label="Videos" id={videoId} />
      <PageHead title="Video" />
      {/* A video's panels always run past one screen, so the footer waits below it. */}
      {refusal !== undefined ? (
        <ReadFailure error={refusal} onRetry={video.reload} />
      ) : (
        <Pending height="100dvh" />
      )}
    </>
  );
}

function Loaded({
  data,
  search,
  selected,
  paging,
  pagingError,
  onRetry,
  seedSize,
  seedOffset,
}: {
  data: VideoDetail;
  search: string;
  selected: number | null;
  paging: boolean;
  pagingError: unknown;
  onRetry: () => void;
  seedSize: number | null;
  seedOffset: number;
}) {
  const { video, frames, shots } = data;
  // The row's tags after a write, read back; never a diff applied here.
  const [written, setWritten] = useState<string[] | null>(null);
  const tags = written ?? video.tags;
  // Open (in the lightbox) and selected (marked) are two facts: a bar marks a
  // frame without opening it.
  const [open, setOpen] = useState<FrameCard | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const closeFrame = useCallback(() => setOpen(null), []);

  const openFrame = useCallback((frame: FrameCard) => {
    setOpen(frame);
    markFrame(frame.ord);
  }, []);

  /** Mark a frame already on this strip page without navigating: scroll to it
   *  and focus its button, the control the next Enter opens. */
  const selectFrame = useCallback((ord: number): boolean => {
    const card = document.getElementById(`frame-${ord}`);
    if (!card) return false;
    markFrame(ord);
    card.scrollIntoView({ block: "center", behavior: "smooth" });
    card.querySelector<HTMLButtonElement>("button")?.focus({ preventScroll: true });
    return true;
  }, []);

  const link = (target: EventTarget | null) => {
    if (root.current) linkShot(root.current, shotOf(target));
  };

  return (
    <div
      className={styles.detail}
      onBlur={(event) => link(event.relatedTarget)}
      onFocus={(event) => link(event.target)}
      onPointerOut={(event) => link(event.relatedTarget)}
      onPointerOver={(event) => link(event.target)}
      ref={root}
    >
      <Title>{video.title}</Title>
      <Crumbs section="videos" label="Videos" id={video.video_id} />

      <Head data={data} tags={tags} />

      <Timeline
        shots={shots.shots}
        capped={shots.capped}
        runtime={video.duration_s ?? 0}
        framePage={frames.limit}
        search={search}
        videoId={video.video_id}
        onSelect={selectFrame}
      />

      <Stored counts={data.counts} origins={data.cue_origins} />

      <Provenance stages={data.stages} />

      <Frames
        frames={frames}
        search={search}
        selected={selected}
        videoId={video.video_id}
        pending={paging}
        error={pagingError}
        onRetry={onRetry}
        onOpen={openFrame}
      />

      <Transcript
        key={video.video_id}
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

      <ManagePanel videoId={video.video_id} tags={tags} onWritten={setWritten} />

      <FrameOverlay onClose={closeFrame} shot={open ? frameShot(open, video.video_id) : null} />
    </div>
  );
}
