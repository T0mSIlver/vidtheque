"use client";

import Link from "next/link";
import styles from "./error.module.css";

// No retry control: re-rendering the tree that just threw rarely helps.
// Copy is positioning.md's; the alternative "Something in the room broke. The
// demo still works." awaits Tom's pick.
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
      {error.digest ? <p className={styles.ref}>ref {error.digest}</p> : null}
    </main>
  );
}
