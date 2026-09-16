import type { Metadata } from "next";
import Link from "next/link";
import styles from "./error.module.css";

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
