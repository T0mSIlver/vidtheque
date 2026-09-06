"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import styles from "./RetryIn.module.css";

// The limiter's delay as a ticking countdown, with the retry disabled until it
// reaches zero. It says only when to try again; it does not explain the limiter
// and it never shows a status code (demo-site.md §6.1). A countdown is machine
// work, so it moves: a frozen number reads as broken where a moving one reads
// as busy.
//
// Two props for the surfaces that are not the demo's search. The dashboard
// reads its payloads in the browser, so refreshing the route would re-render a
// shell and re-run nothing — it hands its own reload in `onRetry` — and it is
// refused for reading a dashboard, not for searching, so it names its own
// limit.
//
// `variant` is the shape, and the demo's is the notice `app.js` drew: a title,
// a detail line that counts, and a "Try again" button beside it. The dashboard
// refuses inside a panel that already has a heading, so there the whole thing
// is one button that counts down in its own label.
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
  const [sent, setSent] = useState(seconds);
  const [left, setLeft] = useState(() => wholeSeconds(seconds));
  // A second refusal on a control that never unmounted is a new wait, not the
  // remainder of the last one: an initialiser runs once, so a retry that came
  // back refused with 58s left kept counting down from the 6s of the first
  // one and re-armed the button early. Re-seeded during render, which is
  // React's own answer to "reset when a prop changes" — and the same one
  // `dashboard/jobs/parts.tsx` gives its two clocks.
  if (sent !== seconds) {
    setSent(seconds);
    setLeft(wholeSeconds(seconds));
  }

  // An effect is the escape hatch for things React does not own: here, a
  // timer. The cleanup runs when the component unmounts or `left` changes,
  // so a countdown never keeps ticking into a page that replaced it.
  useEffect(() => {
    if (left <= 0) return;
    const id = setTimeout(() => setLeft(left - 1), 1000);
    return () => clearTimeout(id);
  }, [left]);

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

/** A floor of one second and the ceiling of a fraction: `retry_after_s` is a
 *  float on the wire, and a 0.4 rendered raw paints "try again" on a bucket
 *  that is still empty — which is one more refused request, not a retry. */
function wholeSeconds(seconds: number): number {
  return Math.max(1, Math.ceil(Number(seconds) || 1));
}
