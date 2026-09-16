"use client";

import Image from "next/image";
import { useId, useRef, useState } from "react";
import type { Shot } from "./Frame";
import { Receipt } from "./Receipt";
import styles from "./Frame.module.css";

/**
 * The enlarge control around a server-rendered thumbnail. `showModal` brings
 * Esc, the inert background and the focus trap; the large image mounts only
 * while the dialog is open. The button is a sibling of the row's anchor,
 * never inside it.
 */
export function FrameLightbox({
  shot,
  large,
  children,
}: {
  shot: Shot;
  large: string;
  children: React.ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const captionId = useId();

  const title = shot.title || shot.video_id;
  const at = shot.timestamp || "0:00";
  const where = [title, shot.channel, at].filter(Boolean).join(" · ");
  const href = shot.link ?? `https://youtu.be/${encodeURIComponent(shot.video_id)}`;

  function show() {
    setOpen(true);
    dialog.current?.showModal();
    // Land on close, not on the first link: Enter must not leave the page.
    dialog.current?.querySelector<HTMLButtonElement>("[data-close]")?.focus();
  }

  function dismiss() {
    setOpen(false);
    trigger.current?.focus();
  }

  return (
    <>
      <button
        type="button"
        ref={trigger}
        className={styles.shotButton}
        aria-label={`Enlarge the frame from ${title} at ${at}`}
        onClick={show}
      >
        {children}
      </button>
      <dialog
        ref={dialog}
        className={styles.shot}
        aria-labelledby={captionId}
        onClose={dismiss}
        // `.inner` covers the whole box, so a click on the dialog itself is
        // a click on the backdrop.
        onClick={(event) => {
          if (event.target === dialog.current) dialog.current?.close();
        }}
      >
        <div className={styles.inner}>
          {open ? (
            <Image
              src={large}
              alt={`Frame from ${title} at ${at}`}
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
