"use client";

import Image from "next/image";
import { useId, useRef, useState, useSyncExternalStore } from "react";
import { Receipt } from "./Receipt";
import styles from "./Frame.module.css";

// A keyframe: a fixed 16:9 box on `--black` with explicit dimensions, so the
// page never shifts when the image lands (CLS 0 is a shipped property).
//
// `unoptimized`: the API already serves sized, cached variants (`?w=320`),
// so routing them through Next's image optimizer would re-encode a JPEG that
// is already the right size and put a second cache in front of the first.
// The size set is the server's; the page asks for one of its widths.
//
// A Client Component, for the two things a picture of somebody's slide has to
// do that markup cannot: fall back to a placeholder when the bytes never
// arrive, and open full size (§6.4). A dead frame URL used to leave a blank
// box, which reads as a corpus with a hole in it rather than as one image.
export function Frame({
  src,
  alt,
  label,
  width = 320,
  priority = false,
}: {
  src: string | null;
  alt: string;
  /** The word a placeholder prints when there is no frame: the channel the
   *  moment came from, rather than an empty rectangle (`lib/group`'s
   *  `channelWord`). */
  label?: string;
  width?: 320 | 960;
  priority?: boolean;
}) {
  const height = Math.round((width * 9) / 16);
  // An image that will not load is a placeholder from then on. `key`ed on the
  // src, so a card re-rendered with a different frame tries the new one.
  const [failed, setFailed] = useState(false);
  const [tried, setTried] = useState(src);
  if (tried !== src) {
    setTried(src);
    setFailed(false);
  }

  if (!src || failed) {
    return (
      <span className={`${styles.box} ${styles.placeholder}`} aria-hidden="true">
        {label ?? "video"}
      </span>
    );
  }
  return (
    <span className={styles.box}>
      <Image
        src={src}
        alt={alt}
        width={width}
        height={height}
        unoptimized
        priority={priority}
        onError={() => setFailed(true)}
        className={styles.img}
      />
    </span>
  );
}

/** What the lightbox needs to know about the moment it is enlarging. */
export interface Shot {
  thumb: string | null;
  thumb_large: string | null;
  title: string;
  channel: string;
  video_id: string;
  timestamp: string;
  link: string | null;
}

// `showModal` brings Esc, the inert background, the focus trap and the modal
// semantics with it, so none of them are written here. Feature-detected once
// and after hydration: with no `showModal` the thumbnail stays the inert image
// it was, rather than becoming a control that half works.
function supportsModal(): boolean {
  return (
    typeof HTMLDialogElement !== "undefined" &&
    typeof HTMLDialogElement.prototype.showModal === "function"
  );
}

// The server has no `HTMLDialogElement` to ask, so it renders the plain image
// and the browser upgrades it on hydration. A store and not an effect, because
// this is one read of the platform rather than a piece of state to synchronise:
// nothing ever changes it, so nothing ever subscribes.
const NEVER_CHANGES = () => () => {};
const NO_MODAL_ON_THE_SERVER = () => false;

/**
 * A thumbnail that opens the frame at `thumb_large` (demo-site.md §6.4).
 *
 * A thumbnail is 160 CSS pixels of somebody's slide: enough to recognise, never
 * enough to *read*. The width is the server's — the page cannot ask for a size
 * of its own — and the large image is mounted only while the dialog is open, so
 * a 960px frame is fetched when a visitor asks for it and released when they
 * close it.
 *
 * The button is a sibling of the row's anchor and never inside it: a button
 * inside an `<a>` is neither valid nor operable, which is what turned the result
 * row into a `<div>` holding three controls.
 */
export function FrameShot({ shot, alt, label }: { shot: Shot; alt: string; label?: string }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const canModal = useSyncExternalStore(NEVER_CHANGES, supportsModal, NO_MODAL_ON_THE_SERVER);
  const [open, setOpen] = useState(false);
  const captionId = useId();

  const large = shot.thumb_large ?? shot.thumb;
  const where = [shot.title || shot.video_id, shot.channel, shot.timestamp || "0:00"]
    .filter(Boolean)
    .join(" · ");
  const href = shot.link ?? `https://youtu.be/${encodeURIComponent(shot.video_id)}`;

  // No frame to enlarge, or a platform that cannot open one: the plain image.
  if (!canModal || !large) return <Frame src={shot.thumb} alt={alt} label={label} />;

  function show() {
    setOpen(true);
    dialog.current?.showModal();
    // `showModal()` would land on the first focusable thing in the dialog,
    // which is the YouTube link — so the first Enter after opening a picture
    // would leave the page. Close is the safe landing.
    dialog.current?.querySelector<HTMLButtonElement>("[data-close]")?.focus();
  }

  function dismiss() {
    setOpen(false);
    // Focus returns to the exact thumbnail that opened it, tracked rather than
    // inferred: clicking a button does not focus it on every browser.
    trigger.current?.focus();
  }

  return (
    <>
      <button
        type="button"
        ref={trigger}
        className={styles.shotButton}
        aria-label={`Enlarge the frame from ${shot.title || shot.video_id} at ${shot.timestamp || "0:00"}`}
        onClick={show}
      >
        {/* The button owns the accessible name, so the image inside it is
            decorative — otherwise a screen reader reads the frame twice. */}
        <Frame src={shot.thumb} alt="" label={label} />
      </button>
      <dialog
        ref={dialog}
        className={styles.shot}
        aria-labelledby={captionId}
        onClose={dismiss}
        // The dialog element is only the backdrop: `.inner` covers every pixel
        // of the box, so a click that lands on the dialog itself landed outside
        // the picture.
        onClick={(event) => {
          if (event.target === dialog.current) dialog.current?.close();
        }}
      >
        <div className={styles.inner}>
          {open ? (
            <Image
              src={large}
              alt={`Frame from ${shot.title || shot.video_id} at ${shot.timestamp || "0:00"}`}
              width={960}
              height={540}
              unoptimized
              className={styles.shotImg}
            />
          ) : null}
          <div className={styles.shotFoot}>
            <p id={captionId} className={styles.shotCaption}>
              {where}
            </p>
            {/* The receipt again, at full size: the picture is the evidence and
                this is where it came from, on the second. */}
            <Receipt href={href} size="lg" className={styles.receipt} />
          </div>
          <button
            type="button"
            data-close
            className={styles.shotClose}
            onClick={() => dialog.current?.close()}
          >
            close
          </button>
        </div>
      </dialog>
    </>
  );
}
