import Link from "next/link";
import styles from "./Rail.module.css";

// The header on the reader surfaces. `Link` is a client-side navigation: the
// browser swaps the page's React tree instead of reloading the document, and
// prefetches the target when the link scrolls into view.
//
// The wordmark goes to `/` — the landing, which is what a wordmark means
// (demo-site.md §6.1) — and the working corpus is `/demo`.
export function Rail() {
  return (
    <header className={styles.rail}>
      <div className={styles.inner}>
        <Link href="/" className={styles.mark}>
          vidtheque<i className={styles.dot}>.</i>
        </Link>
        <nav className={styles.nav} aria-label="Primary">
          <Link href="/demo">search</Link>
          <Link href="/videos">library</Link>
        </nav>
      </div>
    </header>
  );
}
