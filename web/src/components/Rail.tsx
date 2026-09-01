import Link from "next/link";
import styles from "./Rail.module.css";

// The header on every page. `Link` is a client-side navigation: the browser
// swaps the page's React tree instead of reloading the document, and
// prefetches the target when the link scrolls into view.
export function Rail() {
  return (
    <header className={styles.rail}>
      <div className={styles.inner}>
        <Link href="/" className={styles.mark}>
          vidtheque<i className={styles.dot}>.</i>
        </Link>
        <nav className={styles.nav} aria-label="Primary">
          <Link href="/">search</Link>
          <Link href="/videos">library</Link>
        </nav>
      </div>
    </header>
  );
}
