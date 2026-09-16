"use client";

import { useEffect, useRef, type ReactNode } from "react";
import styles from "./landing.module.css";

// Constant drift speed whatever the track width: the duration follows the
// measured half-track.
const SPEED = 4.2; // px per second

export function BandPacer({ children }: { children: ReactNode }) {
  const band = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const node = band.current;
    if (!node) return;
    const still = location.search.includes("still");
    const reduce = still || matchMedia("(prefers-reduced-motion: reduce)").matches;

    const pace = () =>
      node.querySelectorAll<HTMLElement>(`.${styles.wtrack}`).forEach((track) => {
        if (reduce) {
          track.style.animation = "none";
          return;
        }
        const half = track.scrollWidth / 2;
        track.style.setProperty("--shift", half + "px");
        track.style.animationDuration = (half / SPEED).toFixed(0) + "s";
      });

    const frame = requestAnimationFrame(pace);
    let timer: ReturnType<typeof setTimeout>;
    const onResize = () => {
      clearTimeout(timer);
      timer = setTimeout(pace, 200);
    };
    addEventListener("resize", onResize, { passive: true });
    return () => {
      cancelAnimationFrame(frame);
      clearTimeout(timer);
      removeEventListener("resize", onResize);
    };
  }, []);

  return (
    <div className={styles.wallband} ref={band}>
      {children}
    </div>
  );
}
