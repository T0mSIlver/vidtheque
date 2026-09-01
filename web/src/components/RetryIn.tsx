"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import styles from "./RetryIn.module.css";

// The limiter's Retry-After as a ticking countdown, with the retry disabled
// until it reaches zero. It says only when to try again; it does not explain
// the limiter (demo-site.md §6.1). A countdown is machine work, so it moves.
export function RetryIn({ seconds }: { seconds: number }) {
  const router = useRouter();
  const [left, setLeft] = useState(seconds);

  // An effect is the escape hatch for things React does not own: here, a
  // timer. The cleanup runs when the component unmounts or `left` changes,
  // so a countdown never keeps ticking into a page that replaced it.
  useEffect(() => {
    if (left <= 0) return;
    const id = setTimeout(() => setLeft(left - 1), 1000);
    return () => clearTimeout(id);
  }, [left]);

  return (
    <p className={styles.box}>
      <span>Too many searches for now.</span>
      <button
        type="button"
        className={styles.retry}
        disabled={left > 0}
        onClick={() => router.refresh()}
      >
        {left > 0 ? `retry in ${left}s` : "retry"}
      </button>
    </p>
  );
}
