"use client";

import { useId, useState, type ReactNode } from "react";
import controls from "./controls.module.css";
import styles from "./fold.module.css";

/**
 * Content behind a toggle (dashboard.md §24.3). `phone` folds it only on a
 * narrow viewport and shows it whole elsewhere, by CSS, so the server's render
 * and the first paint agree. `open` forces it open, for a deep link into it.
 */
export function Fold({
  label,
  phone = false,
  open: forced = false,
  children,
}: {
  /** What is behind it, e.g. "the filters" or "24 frames". */
  label: ReactNode;
  phone?: boolean;
  open?: boolean;
  children: ReactNode;
}) {
  const [toggled, setToggled] = useState(false);
  const id = useId();
  const open = toggled || forced;
  return (
    <div className={phone ? styles.phone : styles.fold} data-open={open ? "" : undefined}>
      <button
        type="button"
        className={`${controls.ghostlink} ${styles.toggle}`}
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setToggled(!open)}
      >
        {open ? "Hide" : "Show"} {label}
      </button>
      <div className={styles.body} id={id}>
        {children}
      </div>
    </div>
  );
}
