"use client";

import { useEffect, useRef, type ReactNode } from "react";
import styles from "./landing.module.css";

// Rendered asked and unveiled. The question types itself only when the log is
// still below the fold on arrival, so nothing visible is taken back.
export function BoothLog({
  question,
  head,
  children,
}: {
  question: string;
  head: ReactNode;
  children: ReactNode;
}) {
  const term = useRef<HTMLDivElement>(null);
  const full = useRef<HTMLSpanElement>(null);
  const typed = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    const termEl = term.current;
    const fullEl = full.current;
    const askedEl = typed.current;
    if (!termEl || !fullEl || !askedEl || !("IntersectionObserver" in window)) return;
    if (location.search.includes("still")) return;
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    if (termEl.getBoundingClientRect().top < innerHeight) return;

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const sleep = (ms: number) => new Promise<void>((resolve) => (timer = setTimeout(resolve, ms)));

    const settle = () => {
      fullEl.hidden = false;
      askedEl.hidden = true;
      termEl.classList.remove(styles.typingq, styles.veiled);
    };
    askedEl.textContent = "";
    askedEl.hidden = false;
    fullEl.hidden = true;
    termEl.classList.add(styles.veiled);

    const io = new IntersectionObserver(
      (entries) => {
        if (!entries.some((e) => e.isIntersecting)) return;
        io.disconnect();
        void (async () => {
          termEl.classList.add(styles.typingq);
          for (let i = 0; i < question.length; i++) {
            if (cancelled) return;
            askedEl.textContent = question.slice(0, i + 1);
            await sleep(22 + (question[i] === " " ? 12 : 0));
          }
          if (!cancelled) settle();
        })();
      },
      { threshold: 0.22 },
    );
    io.observe(termEl);
    return () => {
      cancelled = true;
      clearTimeout(timer);
      io.disconnect();
      settle();
    };
  }, [question]);

  return (
    <div className={styles.term} ref={term}>
      <div className={styles.termhead}>{head}</div>
      <div className={styles.termbody}>
        <div className={styles.ask}>
          <span className={styles.pfx}>&gt;</span>
          <p className={styles.termq}>
            <span ref={full}>{question}</span>
            <span ref={typed} hidden />
          </p>
          <span className={styles.caret2} />
        </div>
        <div className={styles.termrest}>{children}</div>
      </div>
      <div className={styles.termfoot} />
    </div>
  );
}
