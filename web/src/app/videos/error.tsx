"use client";

import styles from "./page.module.css";

// An error boundary is a Client Component: it has to hold state (errored or
// not) and handle a click. In production the server strips the message of
// any error thrown during render and sends only a `digest`, so what a
// visitor sees here is deliberately generic; the digest matches the server log.
export default function LibraryError({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  return (
    <main className={styles.main}>
      <h1 className={styles.headline}>The library</h1>
      <p className={styles.empty}>
        The corpus did not answer.{" "}
        {error.digest ? <span className={styles.machine}>ref {error.digest}</span> : null}
      </p>
      <button type="button" className={styles.retry} onClick={() => retry()}>
        try again
      </button>
    </main>
  );
}
