import type { Metadata } from "next";
import Link from "next/link";
import styles from "./error.module.css";

// The front door's 404. Python's own answer here was the MCP mount's bare
// `text/plain` "Not Found" — a page from a different product in a different
// type system, which is what the root boundary beside this file was written to
// stop happening on a throw. A mistyped path deserves the same treatment: the
// word, one sentence, and the one door that is still open.
//
// Next renders this for an unmatched path and answers `404` with it, so the
// status a client reads and the page a person reads stay one fact.
export const metadata: Metadata = { title: "No such page" };

export default function NotFound() {
  return (
    <main className={styles.main}>
      <p>
        <b className={styles.mark}>
          vidtheque<i className={styles.dot}>.</i>
        </b>
      </p>
      <h1 className={styles.sentence}>No such page. The demo is where the corpus is.</h1>
      <p className={styles.next}>
        <Link href="/demo">go to the demo</Link>
      </p>
    </main>
  );
}
