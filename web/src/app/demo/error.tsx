"use client";

import ui from "@/components/console/console.module.css";
import styles from "./page.module.css";

// Only a render that threw reaches here; a refused read is a console state.
// Production sends a digest and nothing else, so the message stays generic.
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
      <div className={`${ui.notice} ${ui.noticeBad}`}>
        <p className={ui.noticeTitle}>Could not reach the server.</p>
        {error.digest ? <p className={ui.noticeDetail}>ref {error.digest}</p> : null}
        <button type="button" className={ui.ghost} onClick={() => retry()}>
          Try again
        </button>
      </div>
    </main>
  );
}
