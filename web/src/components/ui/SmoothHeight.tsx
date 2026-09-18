"use client";

import { useLayoutEffect, useRef } from "react";
import styles from "./SmoothHeight.module.css";

/**
 * Follows its content's height with a transition, so a panel that appears, is
 * swapped or grows pushes the page instead of jolting it. The first size is
 * taken as it is; `prefers-reduced-motion` turns the transition off in CSS.
 */
export function SmoothHeight({ children }: { children: React.ReactNode }) {
  const outer = useRef<HTMLDivElement>(null);
  const inner = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const box = outer.current;
    const content = inner.current;
    if (!box || !content || typeof ResizeObserver === "undefined") return;
    box.style.height = `${content.offsetHeight}px`;
    // Armed after the first paint, so a server-rendered panel does not grow in.
    const arm = requestAnimationFrame(() => box.classList.add(styles.armed));
    const watch = new ResizeObserver(() => {
      box.style.height = `${content.offsetHeight}px`;
    });
    watch.observe(content);
    return () => {
      cancelAnimationFrame(arm);
      watch.disconnect();
    };
  }, []);

  return (
    <div ref={outer} className={styles.outer}>
      <div ref={inner} className={styles.inner}>
        {children}
      </div>
    </div>
  );
}
