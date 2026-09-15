"use client";

import { useRouter } from "next/navigation";
import { useTransition } from "react";
import styles from "./page.module.css";

// Page one is the server's, so retrying it is the page asking for itself
// again: `refresh()` re-runs the read at the URL that is on screen, which is
// the act `app.js` performed by calling `runSearch()` a second time.
//
// Every page-1 failure carries one — a bad parameter, a 5xx, a dropped
// connection. They are all one act away from working, and a notice that names
// a failure and offers nothing is where a visitor leaves.
export function TryAgain() {
  const router = useRouter();
  const [pending, start] = useTransition();
  return (
    <button
      type="button"
      className={styles.ghost}
      disabled={pending}
      onClick={() => start(() => router.refresh())}
    >
      Try again
    </button>
  );
}
