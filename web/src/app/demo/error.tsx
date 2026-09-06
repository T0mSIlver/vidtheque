"use client";

import styles from "./page.module.css";

// The last resort, and no longer the search's failure state: a refused read is
// a screen the page draws in the facade's own words (`lib/search`'s outcomes),
// and only a render that actually threw reaches here. The message stays
// generic because in production the server sends a digest and nothing else.
export default function DemoError({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  return (
    <main className={styles.main}>
      <div className={styles.hero}>
        <p className={styles.kick}>
          <s />
          <span>the proof · ai engineer 2026</span>
        </p>
        <h1 className={styles.big}>
          The knowledge of AI Engineer 2026, on tap. <em>Ask it something.</em>
        </h1>
      </div>
      <div className={`${styles.notice} ${styles.noticeBad}`}>
        <p className={styles.noticeTitle}>Could not reach the server.</p>
        {error.digest ? <p className={styles.noticeDetail}>ref {error.digest}</p> : null}
        <button type="button" className={styles.ghost} onClick={() => retry()}>
          Try again
        </button>
      </div>
    </main>
  );
}
