"use client";

import { useEffect, useRef, useState, type ReactNode, type Ref, type RefObject } from "react";
import type { OcrLine } from "@/lib/dashboard/schemas";
import controls from "./kit/controls.module.css";
import styles from "./frame.module.css";

// One keyframe at 1280px, opened where the reader is: the search results and
// the video page share it (dashboard.md §5.2, §5.3). A real `<dialog>` with
// `showModal()`, so the backdrop, focus trap and inertness are the platform's;
// this adds the backdrop click, the focus hand-back and releasing the bytes.

/** One frame, as the page that opened it describes it. */
export interface Shot {
  /** The tool's id; also the remount key that resets which line is lit. */
  frameId: string;
  large: string;
  alt: string;
  caption: ReactNode;
  facts?: ReactNode;
  /** The same second on YouTube, when the moment had one. */
  link: string | null;
  file?: string;
  /** Present, even empty, asks for the OCR layer. */
  lines?: OcrLine[];
}

/** Detection boxes at the stored 0–1 coordinates. Without `onLit` (the card)
 *  they are inert. */
export function OcrBoxes({
  lines,
  lit,
  onLit,
}: {
  lines: OcrLine[];
  lit: number | null;
  onLit?: (index: number | null) => void;
}) {
  return (
    <>
      {lines.map((line, index) => (
        <span
          aria-hidden="true"
          className={`${styles.ocrbox} ${lit === index ? styles.isLit : ""}`}
          data-lit={lit === index || undefined}
          data-ocrbox=""
          key={line.line_no}
          onMouseEnter={onLit && (() => onLit(index))}
          onMouseLeave={onLit && (() => onLit(null))}
          style={{
            left: `${line.box[0] * 100}%`,
            top: `${line.box[1] * 100}%`,
            width: `${(line.box[2] - line.box[0]) * 100}%`,
            height: `${(line.box[3] - line.box[1]) * 100}%`,
          }}
          // The text is listed beside the frame; a node here would cover it.
          title={onLit ? line.text : undefined}
        />
      ))}
    </>
  );
}

/** The lines read off one frame; the index pairs each with its box. */
export function OcrLines({
  className,
  lines,
  lit,
  onLit,
  scroller,
}: {
  className: string;
  lines: OcrLine[];
  lit: number | null;
  onLit: (index: number | null) => void;
  scroller?: Ref<HTMLOListElement>;
}) {
  return (
    <ol className={className} ref={scroller}>
      {lines.map((line, index) => (
        <li
          className={`${styles.ocrline} ${lit === index ? styles.isLit : ""}`}
          data-lit={lit === index || undefined}
          key={line.line_no}
          onBlur={() => onLit(null)}
          onFocus={() => onLit(index)}
          onMouseEnter={() => onLit(index)}
          onMouseLeave={() => onLit(null)}
          tabIndex={0}
        >
          <span className={styles.ocrtext}>{line.text}</span>
          {line.conf !== null ? <span className={styles.conf}>{line.conf.toFixed(2)}</span> : null}
        </li>
      ))}
    </ol>
  );
}

/** Mounted for the life of the page and empty until a frame opens, so the
 *  boxes toggle is kept across opens and closing releases the JPEG. */
export function FrameOverlay({ shot, onClose }: { shot: Shot | null; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const returnTo = useRef<Element | null>(null);
  const [showBoxes, setShowBoxes] = useState(true);

  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (shot) {
      if (!element.open) {
        // Read before `showModal()`, after which the answer is the dialog.
        returnTo.current = document.activeElement;
        element.showModal();
      }
      closeButton.current?.focus();
      return;
    }
    if (!element.open) return;
    element.close();
    // Only once shut: outside an open modal everything is inert.
    const back = returnTo.current;
    returnTo.current = null;
    if (back instanceof HTMLElement) back.focus({ preventScroll: true });
  }, [shot]);

  // `close` does not bubble, so a native listener rather than React's
  // delegated `onClose`.
  useEffect(() => {
    const node = dialog.current;
    if (!node) return;
    const closed = () => onClose();
    node.addEventListener("close", closed);
    return () => node.removeEventListener("close", closed);
  }, [onClose]);

  return (
    <dialog
      aria-labelledby="shot-caption"
      className={styles.shot}
      onClick={(event) => {
        if (event.target === dialog.current) onClose();
      }}
      onKeyDown={(event) => {
        if (event.key !== "Escape") return;
        // Every close runs the one path that hands focus back.
        event.preventDefault();
        onClose();
      }}
      ref={dialog}
    >
      {shot ? (
        <Stage
          closeRef={closeButton}
          key={shot.frameId}
          onClose={onClose}
          onShowBoxes={setShowBoxes}
          shot={shot}
          showBoxes={showBoxes}
        />
      ) : null}
    </dialog>
  );
}

function Stage({
  closeRef,
  onClose,
  onShowBoxes,
  shot,
  showBoxes,
}: {
  closeRef: RefObject<HTMLButtonElement | null>;
  onClose: () => void;
  onShowBoxes: (on: boolean) => void;
  shot: Shot;
  showBoxes: boolean;
}) {
  const lines = useRef<HTMLOListElement>(null);
  const [lit, setLit] = useState<number | null>(null);
  const read = shot.lines;

  /** A box also brings its line into view; a hovered line already is. */
  const light = (index: number | null, fromBox: boolean) => {
    setLit(index);
    if (index === null || !fromBox) return;
    const list = lines.current;
    const line = list?.children[index];
    if (list && line instanceof HTMLElement) reveal(line, list);
  };

  return (
    <div className={styles.shotInner}>
      <div className={styles.shotHead}>
        <div>
          <p className={styles.shotCaption} id="shot-caption">
            {shot.caption}
          </p>
          {shot.facts ? <p className={styles.shotFacts}>{shot.facts}</p> : null}
        </div>
        <button
          className={`${controls.ghostlink} ${styles.shotClose}`}
          onClick={onClose}
          ref={closeRef}
          type="button"
        >
          Close
        </button>
      </div>

      <div className={styles.shotStage}>
        {/* A signed `/frames/…` URL, already sized: not for the optimizer. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img alt={shot.alt} className={styles.shotImg} decoding="async" src={shot.large} />
        {read ? (
          <div className={styles.shotBoxes} hidden={!showBoxes}>
            <OcrBoxes lines={read} lit={lit} onLit={(index) => light(index, true)} />
          </div>
        ) : null}
      </div>

      {read?.length ? (
        <OcrLines
          className={styles.shotLines}
          lines={read}
          lit={lit}
          onLit={(index) => light(index, false)}
          scroller={lines}
        />
      ) : null}

      <div className={styles.shotFoot}>
        {read ? (
          <label className={styles.shotToggle}>
            <input
              checked={showBoxes}
              onChange={(event) => onShowBoxes(event.target.checked)}
              type="checkbox"
            />{" "}
            Show OCR boxes
          </label>
        ) : null}
        <span className={styles.shotLinks}>
          {shot.link ? (
            <a
              className={styles.shotLink}
              href={shot.link}
              rel="noopener noreferrer"
              target="_blank"
            >
              Open at this second
            </a>
          ) : null}
          {shot.file ? (
            <a
              className={styles.shotLink}
              href={shot.file}
              rel="noopener noreferrer"
              target="_blank"
            >
              Open the file
            </a>
          ) : null}
        </span>
      </div>
    </div>
  );
}

/** Scroll the list on its own vertical axis, by the least that shows the line,
 *  and not at all when it is visible — `scrollIntoView` also scrolls inline
 *  (docs/LESSONS.md). */
function reveal(line: HTMLElement, scroller: HTMLElement) {
  const box = scroller.getBoundingClientRect();
  const item = line.getBoundingClientRect();
  if (item.top >= box.top && item.bottom <= box.bottom) return;
  scroller.scrollTop += item.top < box.top ? item.top - box.top : item.bottom - box.bottom;
}
