import Link from "next/link";
import styles from "./Rail.module.css";

const PAGES = [
  { href: "/demo", label: "Sample feed" },
  { href: "/docs", label: "Docs" },
  { href: "/app", label: "App" },
] as const;

// The public site's header: the wordmark home, its three other pages, and one
// quiet slot for what `/api/meta` says about the corpus (demo-site.md §8.4).
export function Rail({ children }: { children?: React.ReactNode }) {
  return (
    <header className={styles.rail}>
      <div className={styles.inner}>
        <Link href="/" className={styles.mark}>
          vidtheque<i className={styles.dot}>.</i>
        </Link>
        <nav className={styles.nav} aria-label="Site">
          {PAGES.map((page) => (
            <Link key={page.href} href={page.href} className={styles.page}>
              {page.label}
            </Link>
          ))}
        </nav>
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
