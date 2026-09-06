"use client";

import { useEffect, useRef } from "react";
import dash from "../dashboard.module.css";
import styles from "./search.module.css";

// The frame lightbox — `templates/search.html`'s `<dialog id="shot">` and the
// delegated opener in `static/dashboard.js:182-237`.
//
// For an OCR or a frame hit the picture *is* the evidence, so it opens where
// the reader is rather than in a tab that has lost the ranking: the still in
// the row is 192px of JPEG in a 128px box, and this is the same keyframe at
// `LIGHTBOX_WIDTH`, where a slide is something you can read.
//
// **A real `<dialog>`, opened with `showModal()`.** Escape, the focus trap, the
// inert background and returning focus to the control that opened it are all
// the platform's, and a hand-built overlay would be four re-implementations of
// them. What is written here is the two things the platform does not do: the
// backdrop click, which is a two-line hit test because `.frameInner` covers
// every pixel of the box, and releasing the bytes on close — a 1280px JPEG per
// frame opened adds up over a browsing session, and nothing needs it once the
// dialog is shut.
//
// **This file belongs beside the video page's own lightbox.** Two pages open
// the same overlay on the same contract; they are separate components only
// because the port split the dashboard between two agents.

export interface Shot {
  /** The tool's own id for the frame, which is what a bug report quotes. */
  frameId: string;
  /** The keyframe at the lightbox width, signed by whoever signed the row's. */
  large: string;
  /** `frame_id · clock · title` — the three facts the caption has always had. */
  caption: string;
  /** The receipt, when the hit had one: the same second out on YouTube. */
  link: string | null;
  alt: string;
}

export function FrameDialog({ shot, onClose }: { shot: Shot | null; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const node = dialog.current;
    if (!node) return;
    if (shot && !node.open) node.showModal();
    if (!shot && node.open) node.close();
  }, [shot]);

  // Escape is the platform's, and the only way back from it is the element's
  // own `close` event. A native listener rather than React's `onClose`: `close`
  // does not bubble, so it is the one dialog event a delegating renderer has
  // had to special-case, and a page whose overlay will not reopen because that
  // special case moved is not a thing to find out in production. `close()`
  // raises it too, and the effect above only calls that while the dialog is
  // open, so the two cannot loop.
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
      className={styles.frameDialog}
      // The dialog element *is* the backdrop: `.frameInner` covers every pixel
      // of the box, so a click that landed on the dialog itself landed outside
      // the picture.
      onClick={(event) => {
        if (event.target === dialog.current) onClose();
      }}
      ref={dialog}
    >
      <div className={styles.frameInner}>
        {/* The caption and the close control belong above the picture, not on
            top of it: the top-right corner of a frame is where a slide puts
            its title. */}
        <div className={styles.frameHead}>
          <p className={styles.frameCaption} id="shot-caption">
            {shot?.caption ?? ""}
          </p>
          <button
            className={`${dash.ghostlink} ${styles.frameClose}`}
            onClick={onClose}
            type="button"
          >
            Close
          </button>
        </div>
        {/* The stage owns the geometry, so the dialog does not resize under the
            pointer while the bytes arrive. */}
        <div className={styles.frameStage}>
          {shot ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img alt={shot.alt} className={styles.frameImg} src={shot.large} />
          ) : null}
        </div>
        <div className={styles.frameFoot}>
          {shot?.link ? (
            <a
              className={styles.frameLink}
              href={shot.link}
              rel="noopener noreferrer"
              target="_blank"
            >
              Open at this second
            </a>
          ) : null}
        </div>
      </div>
    </dialog>
  );
}
