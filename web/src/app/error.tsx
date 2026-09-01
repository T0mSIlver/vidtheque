"use client";

import styles from "./page.module.css";

// The root error boundary: a failed search read that was not a 429. The
// message is deliberately generic; in production the server sends only a
// digest for anything thrown during render.
export default function SearchError({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  return (
    <main className={styles.main}>
      <h1 className={styles.headline}>Ask the talks.</h1>
      <div className={styles.empty}>
        <p>The corpus could not be reached.</p>
        {error.digest ? <p className={styles.count}>ref {error.digest}</p> : null}
        <p>
          <button type="button" className={styles.chip} onClick={() => retry()}>
            try again
          </button>
        </p>
      </div>
    </main>
  );
}
