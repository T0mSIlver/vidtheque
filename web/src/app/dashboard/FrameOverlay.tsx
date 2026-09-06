"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import type { OcrLine } from "@/lib/dashboard/schemas";
import dash from "./dashboard.module.css";
import styles from "./frame.module.css";

// The frame overlay — `search.html`'s and `video.html`'s `<dialog id="shot">`
// and the half of `static/dashboard.js` that filled them (dashboard.md §5.2,
// §5.3).
//
// For an OCR or a frame hit the picture *is* the evidence, so it opens where
// the reader is rather than in a tab that has lost the ranking: the still in
// the row is a thumbnail, and this is the same keyframe at 1280px, where a
// slide is something you can read. Both surfaces asked for exactly that, so
// there is one of it — a search hit and a frame card open the same overlay,
// and the machine's reading of the frame is the optional layer the video page
// adds on top.
//
// **A real `<dialog>`, opened with `showModal()`.** The backdrop, the focus
// trap, the inert background and the modal semantics are the platform's, and a
// hand-built overlay would be four re-implementations of them. What is written
// here is the four things the platform does not do: the backdrop click, which
// is a two-line hit test because `.shotInner` covers every pixel of the box;
// taking Escape so that every close runs the one path that hands the focus
// back; that hand-back itself; and releasing the bytes on close — a 1280px
// JPEG per frame opened adds up over a browsing session, and nothing needs it
// once the dialog is shut.

/** One frame, as the page that opened it describes it. */
export interface Shot {
  /** The tool's own id for the frame, which is what a bug report quotes. It is
   *  also the remount key: which line is lit is reset by opening another
   *  frame, not by an effect that would cascade a second render onto every
   *  open. */
  frameId: string;
  /** The keyframe at the lightbox width, signed by whoever signed the row's. */
  large: string;
  alt: string;
  /** `frame_id · clock · …` — the facts the caption has always carried. */
  caption: ReactNode;
  /** A second, quieter line under it, for a page that has more to say. */
  facts?: ReactNode;
  /** The receipt, when the moment had one: the same second out on YouTube. */
  link: string | null;
  /** The JPEG itself, for a page that offers the file as well as the second. */
  file?: string;
  /** What the machine read off this frame. **Present, even empty, is what asks
   *  for the OCR layer**: the boxes over the still, the lines beside it and the
   *  toggle that turns the boxes off. A page with no reading to show — the
   *  search results — passes nothing and gets the picture and its caption. */
  lines?: OcrLine[];
}

/**
 * The detection boxes over a still, at the 0–1 coordinates the store holds.
 *
 * Shared by the frame card and the overlay because the pairing is the same
 * fact in both, and a second implementation is a second thing to get out of
 * step. The card passes no `onLit`, which is what makes its boxes inert.
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
 * The dialog itself. Mounted for the life of the page and empty until a frame
 * is opened, so the checkbox keeps its answer across opens — which state the
 * boxes are in is a preference, not a fact about one frame — and so closing it
 * releases the JPEG.
 */
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
  }, [shot]);

  // Whatever closed the element — the platform, a `close()` from the effect
  // above, a form inside it — comes back through the element's own `close`
  // event. A native listener rather than React's `onClose`: `close` does not
  // bubble, so it is the one dialog event a delegating renderer has had to
  // special-case, and a page whose overlay will not reopen because that special
  // case moved is not a thing to find out in production. The effect above only
  // calls `close()` while the dialog is open, so the two cannot loop.
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
      // The dialog element *is* the backdrop: `.shotInner` covers every pixel
      // of the box, so a click that landed on the dialog itself landed outside
      // the picture.
      onClick={(event) => {
        if (event.target === dialog.current) onClose();
      }}
      onKeyDown={(event) => {
        if (event.key !== "Escape") return;
        // The platform would close it on its own; taking the key means every
        // close runs the one path that hands the focus back.
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

/** One frame at 1280px: the still with its boxes over it, and its lines beside. */
function Stage({
  closeRef,
  onClose,
  onShowBoxes,
  shot,
  showBoxes,
}: {
  closeRef: React.RefObject<HTMLButtonElement | null>;
  onClose: () => void;
  onShowBoxes: (on: boolean) => void;
  shot: Shot;
  showBoxes: boolean;
}) {
  const lines = useRef<HTMLOListElement>(null);
  const [lit, setLit] = useState<number | null>(null);
  const read = shot.lines;

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
            {shot.caption}
          </p>
          {shot.facts ? <p className={styles.shotFacts}>{shot.facts}</p> : null}
        </div>
        <button
          className={`${dash.ghostlink} ${styles.shotClose}`}
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
