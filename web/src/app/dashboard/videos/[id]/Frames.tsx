"use client";

import { memo, useLayoutEffect, useRef, useState } from "react";
import { Pill } from "@/components/ui/Pill";
import type { FrameCard, VideoDetail } from "@/lib/dashboard/schemas";
import { bytes, clock, count, DASH } from "@/lib/format";
import { OcrBoxes, OcrLines } from "../../FrameOverlay";
import controls from "../../kit/controls.module.css";
import { RefusalNotice } from "../../kit/notice";
import { Pager } from "../../kit/table";
import { Panel, Sep, ui } from "../../kit/ui";
import styles from "./detail.module.css";
import { frameLink } from "./query";

/**
 * Every keyframe on this strip page, its detection boxes at the stored 0–1
 * coordinates and its lines beside it. Frames by URL, never inline base64.
 * While the next page is read the current one stays, dimmed and `aria-busy`.
 */
export function Frames({
  frames,
  search,
  selected,
  videoId,
  pending,
  error,
  onRetry,
  onOpen,
}: {
  frames: VideoDetail["frames"];
  search: string;
  selected: number | null;
  videoId: string;
  /** Another strip page is being read. */
  pending: boolean;
  /** That read's refusal, with the page it replaces still on screen. */
  error: unknown;
  onRetry: () => void;
  onOpen: (frame: FrameCard) => void;
}) {
  // The last page has no Next: its link goes, and focus would fall to <body>.
  const pager = useRef<HTMLDivElement>(null);
  const paged = useRef(false);
  useLayoutEffect(() => {
    if (!paged.current) return;
    paged.current = false;
    if (document.activeElement && document.activeElement !== document.body) return;
    pager.current?.querySelector<HTMLAnchorElement>("a")?.focus({ preventScroll: true });
  }, [frames.offset]);

  return (
    <Panel id="frames" title="Frames, and what the machine read">
      {error !== undefined ? (
        <>
          <RefusalNotice error={error} id="frames-refused" />
          <p>
            <button className={controls.ghostlink} type="button" onClick={onRetry}>
              Try again
            </button>
          </p>
        </>
      ) : null}
      <div className={pending ? styles.paging : undefined} aria-busy={pending || undefined}>
        {frames.frames.length ? (
          <>
            <ul className={styles.frames}>
              {frames.frames.map((frame) => (
                <Card
                  key={frame.frame_id}
                  frame={frame}
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
            <div ref={pager} onClick={() => (paged.current = true)}>
              <Pager
                limit={frames.limit}
                offset={frames.offset}
                hasMore={frames.has_more}
                href={(offset) => frameLink(search, videoId, offset, null, "frames")}
                previous="← Earlier frames"
                next={`Next ${frames.limit} frames →`}
                label="Keyframe pages"
                scroll={false}
              />
            </div>
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
      </div>
    </Panel>
  );
}

const Card = memo(function Card({
  frame,
  videoId,
  selected,
  onOpen,
}: {
  frame: FrameCard;
  videoId: string;
  selected: boolean;
  onOpen: (frame: FrameCard) => void;
}) {
  // Line → box only at card size: a box on a 512px still is not a target.
  const [lit, setLit] = useState<number | null>(null);

  return (
    <li
      className={`${styles.framecard} ${frame.dup_of_ord !== null ? styles.isDup : ""} ${selected ? styles.isSelected : ""}`}
      data-selected={selected || undefined}
      data-shot={frame.shot_id}
      id={`frame-${frame.ord}`}
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
});

/** One frame card as the overlay describes it; `lines`, even empty, asks for
 *  the OCR layer. */
export function frameShot(frame: FrameCard, videoId: string) {
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
