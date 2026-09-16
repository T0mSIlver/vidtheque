import Link from "next/link";
import styles from "./Rail.module.css";

// The reader surfaces' header: the wordmark home, and one quiet slot for what
// `/api/meta` says about the corpus. One link, not a nav (demo-site.md §6.1).
export function Rail({ children }: { children?: React.ReactNode }) {
  return (
    <header className={styles.rail}>
      <div className={styles.inner}>
        <Link href="/" className={styles.mark}>
          vidtheque<i className={styles.dot}>.</i>
        </Link>
        <div className={styles.meta}>{children}</div>
      </div>
    </header>
  );
}

/** The corpus size, and the way into the browsable corpus only where the
 *  server says the route group is there. */
export function RailMeta({ count, browse }: { count?: string | null; browse?: string | null }) {
  return (
    <>
      {count ? <span className={styles.count}>{count}</span> : null}
      {browse ? (
        <a className={styles.browse} href={browse} aria-label="Browse the corpus">
          browse<span className={styles.wide}> the corpus</span> <span aria-hidden="true">→</span>
        </a>
      ) : null}
    </>
  );
}
