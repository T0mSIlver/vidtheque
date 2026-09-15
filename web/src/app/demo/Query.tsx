"use client";

import { Suspense, use, useEffect, useState } from "react";
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
//
// Every read the page makes arrives here as a promise and is consumed by a
// leaf under `<Suspense>`: the box itself must be in the markup at first
// paint, and a component that awaits is a component that is not there yet.

// Reserve the space the results will occupy, so nothing below them moves when
// they land. Results are cards — a video header and its moments — so the shape
// to reserve is a list of moment counts, one entry per card.
//
// What to expect is a guess, and a full page of one-moment cards is the wrong
// one on a small corpus. The best evidence available is what the *last* search
// actually returned, so that is the shape the next one reserves; the first
// search of a session guesses this, which is a ten-hit page over three talks.
export const DEFAULT_SHAPE = [4, 3, 3];

export function Query({
  state,
  whileWaiting = "ready",
  askEnabled,
  shape,
  children,
}: {
  /** The machine's word for how the read ended, once it has. */
  state: Promise<MachineState>;
  /** …and the word the cell prints until then. */
  whileWaiting?: MachineState;
  askEnabled: Promise<boolean>;
  /** Moments per card, as this render's results actually came back. */
  shape?: Promise<number[]> | null;
  children?: React.ReactNode;
}) {
  const [pending, setPending] = useState(false);
  const [last, setLast] = useState<number[]>(DEFAULT_SHAPE);

  return (
    <>
      <SearchBox
        state={state}
        whileWaiting={whileWaiting}
        askEnabled={askEnabled}
        onPending={setPending}
      />
      {shape ? (
        <Suspense fallback={null}>
          <ShapeMemo shape={shape} remember={setLast} />
        </Suspense>
      ) : null}
      {/* The Answer region is in the markup in both modes, as it was in
          `index.html`: a live region a screen reader met for the first time
          when it already had content in it is one it may not announce. Search
          answers in the list below, so here it is empty and hidden. */}
      <section aria-live="polite" aria-label="Answer" hidden />
      {pending ? <Skeleton shape={last} /> : children}
    </>
  );
}

// What the page reserves next time, taken from what came back this time. A
// leaf, because `use` suspends whatever calls it and the query bar above it
// must not be a thing that suspends.
function ShapeMemo({
  shape,
  remember,
}: {
  shape: Promise<number[]>;
  remember: (shape: number[]) => void;
}) {
  const reading = use(shape);
  const key = reading.join(",");
  useEffect(() => {
    if (reading.length) remember(reading);
  }, [key, reading, remember]);
  return null;
}

export function Skeleton({ shape }: { shape: number[] }) {
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
