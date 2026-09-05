"use client";

import Link from "next/link";
import styles from "./error.module.css";

// The root boundary. `/` is the landing and it has had none since the reader
// moved to `/demo` — a throw under it fell through to Next's own screen, which
// is a different product's page in a different type system.
//
// It is deliberately the smallest thing that can be true: the word, one
// sentence, and the one door that is still open. No retry button — the sentence
// says reload, and a control that re-renders the tree that just threw is a
// button that usually does nothing.
//
// The copy is `docs/design/positioning.md`'s to settle, so Tom picks between:
//   A (shipped) — "The projection room went dark. Reload, or go to the demo."
//   B           — "Something in the room broke. The demo still works."
// Both keep the room, name no machinery, and use none of the banned words.
export default function RootError({ error }: { error: Error & { digest?: string } }) {
  return (
    <main className={styles.main}>
      <p>
        <b className={styles.mark}>
          vidtheque<i className={styles.dot}>.</i>
        </b>
      </p>
      <h1 className={styles.sentence}>The projection room went dark. Reload, or go to the demo.</h1>
      <p className={styles.next}>
        <Link href="/demo">go to the demo</Link>
      </p>
      {/* In production the server sends a digest and nothing else, which is the
          only string on this page worth quoting into a report. */}
      {error.digest ? <p className={styles.ref}>ref {error.digest}</p> : null}
    </main>
  );
}
