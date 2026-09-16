"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import styles from "./RetryIn.module.css";

// The limiter's delay as a ticking countdown, with the retry disabled until it
// runs out. It says when to try again and never shows a status code
// (demo-site.md §6.1). `onRetry` is for a page whose data did not come through
// the router; `variant="notice"` is the demo's titled shape.
export function RetryIn({
  seconds,
  message = "Too many requests.",
  variant = "compact",
  onRetry,
}: {
  seconds: number;
  message?: string;
  variant?: "compact" | "notice";
  onRetry?: () => void;
}) {
  const router = useRouter();
  const left = useTicking(wholeSeconds(seconds), true, -1) ?? 0;
  const retry = () => (onRetry ? onRetry() : router.refresh());

  if (variant === "notice") {
    return (
      <div className={`${styles.notice} ${styles.bad}`}>
        <p className={styles.title}>{message}</p>
        <p className={styles.detail}>{left > 0 ? `Try again in ${left}s.` : "Try again."}</p>
        <button type="button" className={styles.ghost} disabled={left > 0} onClick={retry}>
          Try again
        </button>
      </div>
    );
  }

  return (
    <p className={styles.box}>
      <span>{message}</span>
      <button type="button" className={styles.retry} disabled={left > 0} onClick={retry}>
        {left > 0 ? `retry in ${left}s` : "retry"}
      </button>
    </p>
  );
}

/**
 * A number of seconds a payload named, counting by `step` each second while
 * `moving`. A new `seconds` is a new count, re-seeded during render so the old
 * number never paints; a countdown stops at zero.
 */
export function useTicking(seconds: number | null, moving: boolean, step: 1 | -1): number | null {
  const [sent, setSent] = useState(seconds);
  const [now, setNow] = useState(seconds);
  if (sent !== seconds) {
    setSent(seconds);
    setNow(seconds);
  }

  const spent = step === -1 && now !== null && now <= 0;

  useEffect(() => {
    if (!moving || seconds === null || spent) return;
    const id = setInterval(() => setNow((value) => Math.max(0, (value ?? 0) + step)), 1000);
    return () => clearInterval(id);
  }, [moving, seconds, step, spent]);

  return now;
}

/** At least a second, and a fraction rounds up: `retry_after_s` is a float,
 *  and a 0.4 painted as "retry" is one more refused request. */
function wholeSeconds(seconds: number): number {
  return Math.max(1, Math.ceil(Number(seconds) || 1));
}
