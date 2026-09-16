"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { STATS } from "./data/corpus";
import { num, pad } from "@/lib/format/landing";
import styles from "./landing.module.css";

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

// Rendered at the real figures. The count-up only arms while the ledger is
// still below the fold, so a figure never visibly rewinds.
export function Ledger() {
  const ref = useRef<HTMLDListElement>(null);
  const [progress, setProgress] = useState(1);

  useEffect(() => {
    const node = ref.current;
    if (!node || !("IntersectionObserver" in window)) return;
    if (location.search.includes("still")) return;
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    if (node.getBoundingClientRect().top < innerHeight) return;

    let frame = 0;
    setProgress(0);
    const io = new IntersectionObserver(
      (entries) => {
        if (!entries.some((e) => e.isIntersecting)) return;
        io.disconnect();
        const t0 = performance.now();
        const tick = (now: number) => {
          const p = Math.min(1, (now - t0) / 950);
          setProgress(1 - (1 - p) ** 3);
          if (p < 1) frame = requestAnimationFrame(tick);
        };
        frame = requestAnimationFrame(tick);
      },
      { threshold: 0.4 },
    );
    io.observe(node);
    return () => {
      io.disconnect();
      cancelAnimationFrame(frame);
    };
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
