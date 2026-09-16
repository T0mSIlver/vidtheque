"use client";

import { BadNotice } from "@/components/public/console/Notice";
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
      <BadNotice
        title="Could not reach the server."
        detail={error.digest ? `ref ${error.digest}` : undefined}
        onRetry={() => retry()}
      />
    </main>
  );
}
