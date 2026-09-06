"use client";

import { useEffect, useRef, useState } from "react";
import type { FrameCard, OcrLine } from "@/lib/dashboard/schemas";
import { bytes, clock, DASH } from "@/lib/format";
import styles from "./detail.module.css";

// The enlarged frame — `templates/video.html`'s `<dialog id="shot">` and the
// half of `static/dashboard.js` that filled it (dashboard.md §5.3).
//
// A frame card is 512px wide, which is a size you scan; this is 1280px, which
// is a size you read a slide off. That difference is the whole reason the
// two-way box↔line linkage lives here: at card size a detection box is a few
// millimetres of screen and a pointer aimed at one would only be stealing the
// click that opens the frame, so the boxes there are `pointer-events: none`
// and only a line lights its box. Here the boxes are targets, so pointing at
// one lights its line and scrolls it into view, and pointing at a line lights
// its box.
//
// A native `<dialog>` opened with `showModal()`: the backdrop, the focus trap
// and the modal semantics are the platform's, and Escape is the platform's
// too — the key handler below is what makes the close path a single one this
// component owns, so the element that opened the dialog always gets the focus
// back.

/**
 * The detection boxes over a still, at the 0–1 coordinates the store holds.
 *
 * Shared by the card and the dialog because the pairing is the same fact in
 * both, and a second implementation is a second thing to get out of step. The
 * card passes no `onLit`, which is what makes its boxes inert.
 */
export function OcrBoxes({
  lines,
  lit,
  onLit,
}: {
  lines: OcrLine[];
  lit: number | null;
  /** Only the enlarged frame passes this: at card size a box is not a target. */
  onLit?: (index: number | null) => void;
}) {
  return (
    <>
      {lines.map((line, index) => (
        <span
          aria-hidden="true"
          className={`${styles.ocrbox} ${lit === index ? styles.isLit : ""}`}
          key={line.line_no}
          onMouseEnter={onLit && (() => onLit(index))}
          onMouseLeave={onLit && (() => onLit(null))}
          style={{
            left: `${line.box[0] * 100}%`,
            top: `${line.box[1] * 100}%`,
            width: `${(line.box[2] - line.box[0]) * 100}%`,
            height: `${(line.box[3] - line.box[1]) * 100}%`,
          }}
          // A title, not a label: the text is listed beside the frame, and a
          // text node here would sit on top of the thing it describes.
          title={onLit ? line.text : undefined}
        />
      ))}
    </>
  );
}

/**
 * Every line the machine read off one frame, in the order it read them.
 *
 * `line_no` is the key and the index is the linkage: the box drawn over the
 * still carries the same number, which is what makes the pairing hold for
 * every line rather than for the handful a stylesheet could enumerate.
 */
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
  scroller?: React.Ref<HTMLOListElement>;
}) {
  return (
    <ol className={className} ref={scroller}>
      {lines.map((line, index) => (
        <li
          className={`${styles.ocrline} ${lit === index ? styles.isLit : ""}`}
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

/**
 * The dialog itself. Mounted for the life of the page and empty until a card
 * is clicked, so the checkbox keeps its answer across opens: which state the
 * boxes are in is a preference, not a fact about one frame.
 */
export function Lightbox({
  frame,
  videoId,
  onClose,
}: {
  frame: FrameCard | null;
  videoId: string;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const returnTo = useRef<Element | null>(null);
  const [showBoxes, setShowBoxes] = useState(true);

  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (frame) {
      if (!element.open) {
        // What the reader was on before the dialog took the focus trap. Read
        // before `showModal()`, because after it the answer is the dialog.
        returnTo.current = document.activeElement;
        element.showModal();
      }
      closeButton.current?.focus();
      return;
    }
    if (!element.open) return;
    element.close();
    // Only once the dialog is shut: everything outside an open modal is inert,
    // so a `focus()` before `close()` lands nowhere.
    const back = returnTo.current;
    returnTo.current = null;
    if (back instanceof HTMLElement) back.focus({ preventScroll: true });
  }, [frame]);

  return (
    <dialog
      aria-labelledby="shot-caption"
      className={styles.shot}
      onClick={(event) => {
        // `.shotInner` covers every pixel of the box, so a click that landed
        // on the dialog itself landed on the backdrop.
        if (event.target === dialog.current) onClose();
      }}
      onClose={onClose}
      onKeyDown={(event) => {
        if (event.key !== "Escape") return;
        // The platform would close it on its own; taking the key means every
        // close runs the one path that hands the focus back.
        event.preventDefault();
        onClose();
      }}
      ref={dialog}
    >
      {/* Keyed by the frame, so which line is lit is reset by the remount
          rather than by an effect that would cascade a second render onto
          every open. */}
      {frame ? (
        <Stage
          closeRef={closeButton}
          frame={frame}
          key={frame.frame_id}
          onClose={onClose}
          onShowBoxes={setShowBoxes}
          showBoxes={showBoxes}
          videoId={videoId}
        />
      ) : null}
    </dialog>
  );
}

/** One frame at 1280px: the still with its boxes over it, and its lines beside. */
function Stage({
  closeRef,
  frame,
  onClose,
  onShowBoxes,
  showBoxes,
  videoId,
}: {
  closeRef: React.RefObject<HTMLButtonElement | null>;
  frame: FrameCard;
  onClose: () => void;
  onShowBoxes: (on: boolean) => void;
  showBoxes: boolean;
  videoId: string;
}) {
  const lines = useRef<HTMLOListElement>(null);
  const [lit, setLit] = useState<number | null>(null);

  /** Light the box and the line that share an index; a box also brings its
   *  line into view, because a line hovering itself is already where the
   *  reader is looking. */
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
            {frame.frame_id}
            {" · "}
            {clock(frame.t_s)}
            {frame.width && frame.height ? ` · ${frame.width}×${frame.height}` : ""}
            {frame.jpeg_bytes === null ? "" : ` · ${bytes(frame.jpeg_bytes)}`}
          </p>
          <p className={styles.shotFacts}>
            shot {frame.shot_id}
            {" · "}
            {frame.dup_of_ord !== null
              ? `duplicate of #${frame.dup_of_ord}`
              : `sharpness ${frame.sharpness === null ? DASH : frame.sharpness.toFixed(1)}`}
            {" · "}
            {frame.ocr_state}
            {frame.lines.length ? ` · ${frame.lines.length} line(s)` : ""}
          </p>
        </div>
        <button
          className={`${styles.ghostlink} ${styles.shotClose}`}
          onClick={onClose}
          ref={closeRef}
          type="button"
        >
          Close
        </button>
      </div>

      {/* The stage owns the geometry, so a box can never land outside the
          picture and the dialog does not resize under the pointer while the
          bytes arrive. */}
      <div className={styles.shotStage}>
        {/* A signed, expiring `/frames/…` URL on Python's origin, already sized
            by the API. The optimizer would fetch and cache it past its own
            signature. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          alt={`Keyframe ${frame.ord} at ${clock(frame.t_s)}`}
          className={styles.shotImg}
          decoding="async"
          src={frame.large}
        />
        <div className={styles.shotBoxes} hidden={!showBoxes}>
          <OcrBoxes lines={frame.lines} lit={lit} onLit={(index) => light(index, true)} />
        </div>
      </div>

      {frame.lines.length ? (
        <OcrLines
          className={styles.shotLines}
          lines={frame.lines}
          lit={lit}
          onLit={(index) => light(index, false)}
          scroller={lines}
        />
      ) : null}

      <div className={styles.shotFoot}>
        <label className={styles.shotToggle}>
          <input
            checked={showBoxes}
            onChange={(event) => onShowBoxes(event.target.checked)}
            type="checkbox"
          />{" "}
          Show OCR boxes
        </label>
        <span className={styles.shotLinks}>
          <a
            className={styles.shotLink}
            href={`https://youtu.be/${videoId}?t=${Math.floor(frame.t_s)}`}
            rel="noopener noreferrer"
            target="_blank"
          >
            Open at this second
          </a>
          <a
            className={styles.shotLink}
            href={frame.large}
            rel="noopener noreferrer"
            target="_blank"
          >
            Open the file
          </a>
        </span>
      </div>
    </div>
  );
}

/**
 * Bring a line into view **only when it is not already in it**.
 *
 * `scrollIntoView({ block: "nearest" })` was the obvious call and it was the
 * bug (Tom, 2026-08-10): `inline` defaults to `"nearest"` too, so moving the
 * pointer between two adjacent detection boxes slid the whole list sideways
 * under the reader. This scrolls the scroller itself, on one axis, by the
 * smallest amount that puts the line inside — and does nothing at all when it
 * is inside already. Instant, never smooth: it is a correction, not a journey,
 * which is also what `prefers-reduced-motion` asks of it.
 */
function reveal(line: HTMLElement, scroller: HTMLElement) {
  const box = scroller.getBoundingClientRect();
  const item = line.getBoundingClientRect();
  if (item.top >= box.top && item.bottom <= box.bottom) return;
  scroller.scrollTop += item.top < box.top ? item.top - box.top : item.bottom - box.bottom;
}
