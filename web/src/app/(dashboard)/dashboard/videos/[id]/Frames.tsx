"use client";

import { memo, useCallback, useEffect, useRef, useState } from "react";
import { Pill } from "@/components/ui/Pill";
import { dashboard } from "@/lib/dashboard/client";
import type { FrameCard, VideoDetail } from "@/lib/dashboard/schemas";
import { bytes, clock, DASH } from "@/lib/format";
import { OcrBoxes } from "@/components/dashboard/FrameOverlay";
import controls from "@/components/dashboard/kit/controls.module.css";
import { RefusalNotice } from "@/components/dashboard/kit/notice";
import { Panel } from "@/components/dashboard/kit/ui";
import styles from "./detail.module.css";

/** OCR states that need no word on the card: read, or read and blank. */
const QUIET_OCR = new Set(["done", "empty"]);

type Page = VideoDetail["frames"];

/**
 * The kept keyframes as one strip that scrolls sideways, each with its
 * detection boxes. Pages after the one the URL asked for are read as the strip
 * nears its end and appended (§28.7); a deduplicated frame is not drawn, and
 * selecting one selects the frame it duplicates.
 */
export function Frames({
  frames,
  selected,
  videoId,
  pending,
  error,
  onRetry,
  onOpen,
}: {
  frames: Page;
  selected: number | null;
  videoId: string;
  /** Another strip page is being read for the URL. */
  pending: boolean;
  /** That read's refusal, with the page it replaces still on screen. */
  error: unknown;
  onRetry: () => void;
  onOpen: (frame: FrameCard) => void;
}) {
  const strip = useStrip(frames, videoId);
  const { more } = strip;
  const scroller = useRef<HTMLOListElement>(null);
  const [ends, setEnds] = useState({ start: true, end: true });

  const kept = strip.frames.filter((frame) => frame.dup_of_ord === null);
  const target = keptOrd(strip.frames, selected);

  const measure = useCallback(() => {
    const element = scroller.current;
    if (!element) return;
    const start = element.scrollLeft <= 1;
    const end = element.scrollLeft + element.clientWidth >= element.scrollWidth - 1;
    setEnds((last) => (last.start === start && last.end === end ? last : { start, end }));
    // A strip's width short of its end, so the next page lands before the stop.
    if (element.scrollLeft + element.clientWidth * 2 >= element.scrollWidth) more();
  }, [more]);

  useEffect(() => {
    measure();
    const element = scroller.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [measure, kept.length]);

  const step = (direction: 1 | -1) => {
    const element = scroller.current;
    if (element)
      element.scrollBy({ left: direction * element.clientWidth * 0.8, behavior: "smooth" });
  };

  return (
    <Panel id="frames" title="Keyframes">
      {error !== undefined || strip.error !== undefined ? (
        <>
          <RefusalNotice error={error ?? strip.error} id="frames-refused" />
          <p>
            <button
              className={controls.ghostlink}
              type="button"
              onClick={error !== undefined ? onRetry : strip.retry}
            >
              Try again
            </button>
          </p>
        </>
      ) : null}
      {kept.length || strip.earlier ? (
        <div
          className={`${styles.strip} ${pending ? styles.paging : ""}`}
          aria-busy={pending || strip.busy || undefined}
        >
          <ol className={styles.frames} onScroll={measure} ref={scroller}>
            {strip.earlier ? (
              <li className={styles.earlier}>
                <button className={controls.ghostlink} type="button" onClick={strip.back}>
                  Earlier keyframes
                </button>
              </li>
            ) : null}
            {kept.map((frame) => (
              <Card
                key={frame.frame_id}
                frame={frame}
                onOpen={onOpen}
                selected={frame.ord === target}
                videoId={videoId}
              />
            ))}
          </ol>
          <StripArrow side="start" hidden={ends.start} onClick={() => step(-1)} />
          <StripArrow side="end" hidden={ends.end} onClick={() => step(1)} />
        </div>
      ) : (
        <p className={styles.emptyLead}>No keyframes.</p>
      )}
      {/* The page's line budget ran out: §5.3's double cap. */}
      {strip.capped ? (
        <p className={styles.panelNote}>
          Some frames list fewer lines than they hold: the page&apos;s on-screen-text budget ran
          out.
        </p>
      ) : null}
    </Panel>
  );
}

/** A scroll control drawn, not typed: a chevron in one stroke. */
function StripArrow({
  side,
  hidden,
  onClick,
}: {
  side: "start" | "end";
  hidden: boolean;
  onClick: () => void;
}) {
  return (
    <button
      aria-label={side === "start" ? "Scroll back" : "Scroll on"}
      className={`${styles.arrow} ${side === "start" ? styles.arrowStart : styles.arrowEnd}`}
      hidden={hidden}
      onClick={onClick}
      tabIndex={-1}
      type="button"
    >
      <svg aria-hidden="true" height="16" viewBox="0 0 16 16" width="16">
        <path
          d={side === "start" ? "M10 3 5 8l5 5" : "M6 3l5 5-5 5"}
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
        />
      </svg>
    </button>
  );
}

/** A selected duplicate stands for the frame it duplicates, which is drawn. */
export function keptOrd(frames: FrameCard[], selected: number | null): number | null {
  if (selected === null) return null;
  const frame = frames.find((entry) => entry.ord === selected);
  return frame?.dup_of_ord ?? selected;
}

/** The strip's frames: the URL's page, then pages read after it in place. */
function useStrip(first: Page, videoId: string) {
  const [state, setState] = useState({
    seed: first,
    frames: first.frames,
    start: first.offset,
    next: first.offset + first.limit,
    hasMore: first.has_more,
    capped: first.ocr_lines_capped,
    busy: false,
    error: undefined as unknown,
  });
  // A new page from the URL (a timeline jump) starts the strip again.
  if (state.seed !== first) {
    setState({
      seed: first,
      frames: first.frames,
      start: first.offset,
      next: first.offset + first.limit,
      hasMore: first.has_more,
      capped: first.ocr_lines_capped,
      busy: false,
      error: undefined,
    });
  }

  const request = useRef<AbortController | null>(null);
  // Synchronous, unlike `busy`: a scroll that fires twice before a render
  // must not start a second read.
  const reading = useRef(false);
  useEffect(() => () => request.current?.abort(), []);

  const read = useCallback(
    (offset: number, back: boolean) => {
      request.current?.abort();
      const controller = new AbortController();
      request.current = controller;
      reading.current = true;
      setState((last) => ({ ...last, busy: true, error: undefined }));
      // The strip's own size, at another offset; the rest of the payload is
      // read again and dropped.
      const query = new URLSearchParams({
        frames: String(first.limit),
        frame_offset: String(offset),
      });
      dashboard.video(videoId, query, controller.signal).then(
        (data) => {
          if (controller.signal.aborted) return;
          reading.current = false;
          const page = data.frames;
          setState((last) =>
            back
              ? {
                  ...last,
                  frames: [
                    ...page.frames.filter((frame) => frame.ord < last.start),
                    ...last.frames,
                  ],
                  start: page.offset,
                  capped: last.capped || page.ocr_lines_capped,
                  busy: false,
                }
              : {
                  ...last,
                  // Only frames past the strip's end: a page that overlaps it,
                  // or answers for another offset, adds nothing twice.
                  frames: [
                    ...last.frames,
                    ...page.frames.filter((frame) => frame.ord >= last.next),
                  ],
                  next: Math.max(last.next, page.offset + page.frames.length),
                  hasMore: page.has_more && page.offset + page.frames.length > last.next,
                  capped: last.capped || page.ocr_lines_capped,
                  busy: false,
                },
          );
        },
        (error: unknown) => {
          if (controller.signal.aborted) return;
          reading.current = false;
          setState((last) => ({ ...last, busy: false, error }));
        },
      );
    },
    [first.limit, videoId],
  );

  const { busy, hasMore, next, start, error } = state;
  const more = useCallback(() => {
    if (!reading.current && hasMore && error === undefined) read(next, false);
  }, [hasMore, next, error, read]);
  // The reader asked for this one: it replaces a read the scroll started.
  const back = useCallback(
    () => read(Math.max(start - first.limit, 0), true),
    [start, first.limit, read],
  );
  const retry = useCallback(() => read(next, false), [next, read]);

  return {
    frames: state.frames,
    capped: state.capped,
    busy,
    error,
    earlier: start > 0,
    more,
    back,
    retry,
  };
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
  return (
    <li
      className={`${styles.framecard} ${selected ? styles.isSelected : ""}`}
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
        <OcrBoxes lines={frame.lines} lit={null} />
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
        {QUIET_OCR.has(frame.ocr_state) ? null : <Pill state={frame.ocr_state} />}
      </p>
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
