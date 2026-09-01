import Link from "next/link";
import styles from "./page.module.css";

export default function VideoNotFound() {
  return (
    <main className={styles.main}>
      <p className={styles.kicker}>404</p>
      <h1 className={styles.title}>No video by that id</h1>
      <p className={styles.lede}>
        Nothing in this corpus has it. <Link href="/videos">Browse the library</Link> instead.
      </p>
    </main>
  );
}
