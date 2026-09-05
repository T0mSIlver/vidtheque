"use client";

import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { STATS } from "@/landing/corpus";
import { num, pad } from "@/landing/format";
import styles from "./landing.module.css";

// The rewind has to happen before the browser paints, and this component is
// server-rendered too — where a layout effect does nothing and React says so.
const useBeforePaint = typeof window === "undefined" ? useEffect : useLayoutEffect;

// The ledger — the machine shows its books, and the figures count up when you
// reach them. Motion only where the machine is working (the Motion Law): the
// count is the read happening, not decoration.
type Cell = { label: string; target: number; render: (v: number) => ReactNode };

const CELLS: Cell[] = [
  { label: "talks", target: STATS.talks, render: (v) => num(v) },
  {
    label: "hours watched",
    target: STATS.seconds / 3600,
    render: (v) => (
      <>
        {Math.floor(v)}
        <u>h</u> {pad(Math.round((v % 1) * 60))}
        <u>m</u>
      </>
    ),
  },
  { label: "moments kept", target: STATS.keyframes, render: (v) => num(v) },
  { label: "lines read off the screen", target: STATS.ocr_lines, render: (v) => num(v) },
];

export function Ledger() {
  const ref = useRef<HTMLDListElement>(null);
  // Server-rendered at the real figures, so the books are readable without
  // JavaScript. The count-up rewinds them to zero before the browser paints.
  const [progress, setProgress] = useState(1);

  useBeforePaint(() => {
    if (!canAnimate()) return;
    setProgress(0);
  }, []);

  useEffect(() => {
    const node = ref.current;
    if (!node || !canAnimate()) return;
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (!e.isIntersecting) return;
          io.disconnect();
          const t0 = performance.now();
          const dur = 950;
          const tick = (now: number) => {
            const p = Math.min(1, (now - t0) / dur);
            setProgress(1 - (1 - p) ** 3);
            if (p < 1) requestAnimationFrame(tick);
          };
          requestAnimationFrame(tick);
        });
      },
      { threshold: 0.4 },
    );
    io.observe(node);
    return () => io.disconnect();
  }, []);

  return (
    <dl className={styles.ledger} ref={ref}>
      {CELLS.map((c) => (
        <div key={c.label}>
          <dt>{c.label}</dt>
          <dd>{c.render(c.target * progress)}</dd>
        </div>
      ))}
    </dl>
  );
}

// `?still=1` and reduced motion are the same switch: the page paints its end
// state at once. No IntersectionObserver (jsdom, old browsers) means the same.
function canAnimate() {
  if (typeof window === "undefined") return false;
  if (!("IntersectionObserver" in window)) return false;
  if (location.search.includes("still")) return false;
  return !matchMedia("(prefers-reduced-motion: reduce)").matches;
}
