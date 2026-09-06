"use client";

import { useState } from "react";
import { SearchBox, type MachineState } from "@/components/SearchBox";
import styles from "./page.module.css";

// The query bar and whatever is under it, on one client boundary, because the
// two are one state while a search is in flight: the box keeps the query and
// the caret, and the space below it reserves the shape the results will take.
//
// The alternative — a `<Suspense>` around the results alone — cannot do this.
// A navigation started in a transition never shows a fallback (that is what a
// transition is for), so the skeleton would only ever appear on a cold load,
// which is the one moment there is nothing to reserve *for*.

// Reserve the space the results will occupy, so nothing below them moves when
// they land. Results are cards — a video header and its moments — so the shape
// to reserve is a list of moment counts, one entry per card.
//
// What to expect is a guess, and a full page of one-moment cards is the wrong
// one on a small corpus. The best evidence available is what the *last* search
// actually returned, so that is the shape the next one reserves; the first
// search of a session guesses this, which is a ten-hit page over three talks.
const DEFAULT_SHAPE = [4, 3, 3];

export function Query({
  state,
  askEnabled,
  shape,
  children,
}: {
  state: MachineState;
  askEnabled: boolean;
  /** Moments per card, as this render's results actually came back. */
  shape?: number[];
  children?: React.ReactNode;
}) {
  const [pending, setPending] = useState(false);
  // Remembered across renders, and taken from a fresh render rather than from
  // an effect: the shape arrives as a prop, so the reservation is a value this
  // component derives and not a side effect it performs. `shape` is a new array
  // every render, so what is compared is its reading.
  const reading = shape?.join(",") ?? "";
  const [seen, setSeen] = useState(reading);
  const [last, setLast] = useState<number[]>(shape?.length ? shape : DEFAULT_SHAPE);
  if (!pending && reading !== seen) {
    setSeen(reading);
    if (shape?.length) setLast(shape);
  }

  return (
    <>
      <SearchBox state={state} askEnabled={askEnabled} onPending={setPending} />
      {pending ? <Skeleton shape={last} /> : children}
    </>
  );
}

function Skeleton({ shape }: { shape: number[] }) {
  return (
    <>
      <p className={styles.status} role="status">
        searching…
      </p>
      <div className={styles.results} aria-busy="true" aria-label="Searching">
        {shape.map((moments, card) => (
          // They fade down the list, so a page of grey reads as a reserve
          // rather than as a wall.
          <div key={card} className={styles.skelCard} data-depth={Math.min(card, 3)}>
            <div className={styles.skelHead}>
              <span className={styles.skelThumb} />
              <span className={styles.skelLines}>
                <span className={`${styles.skelLine} ${styles.w70}`} />
                <span className={`${styles.skelLine} ${styles.w40}`} />
              </span>
            </div>
            {Array.from({ length: moments }, (_, moment) => (
              <span key={moment} className={`${styles.skelMoment} ${styles.w90}`} />
            ))}
          </div>
        ))}
      </div>
    </>
  );
}
