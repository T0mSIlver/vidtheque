"use client";

import { useEffect, useRef, type ReactNode } from "react";
import styles from "./landing.module.css";

// BEAT 4 — the booth log. The transcript is server-rendered and passed in; what
// this owns is the one piece of motion the inventory lists for this beat: the
// question types itself when you reach the terminal, and the log behind it
// unveils once it has been asked. Under reduced motion or `?still=1` both are
// already there.
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
  const asked = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    const termEl = term.current;
    const askedEl = asked.current;
    if (!termEl || !askedEl) return;

    let cancelled = false;
    const timers = new Set<ReturnType<typeof setTimeout>>();
    const sleep = (ms: number) =>
      new Promise<void>((resolve) => {
        const id = setTimeout(() => {
          timers.delete(id);
          resolve();
        }, ms);
        timers.add(id);
      });

    const finish = () => {
      askedEl.textContent = question;
      termEl.classList.remove(styles.veiled, styles.typingq);
    };

    const still = location.search.includes("still");
    const reduce = still || matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce || !("IntersectionObserver" in window)) {
      finish();
      return;
    }

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
          if (cancelled) return;
          termEl.classList.remove(styles.typingq);
          termEl.classList.remove(styles.veiled);
        })();
      },
      { threshold: 0.22 },
    );
    io.observe(termEl);
    return () => {
      cancelled = true;
      timers.forEach(clearTimeout);
      timers.clear();
      io.disconnect();
    };
  }, [question]);

  return (
    <div className={`${styles.term} ${styles.veiled}`} ref={term}>
      <div className={styles.termhead}>{head}</div>
      <div className={styles.termbody}>
        <div className={styles.ask}>
          <span className={styles.pfx}>&gt;</span>
          <p className={styles.termq} ref={asked} />
          <span className={styles.caret2} />
        </div>
        <div className={styles.termrest}>{children}</div>
      </div>
      <div className={styles.termfoot} />
    </div>
  );
}
