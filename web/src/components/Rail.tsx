import Link from "next/link";
import styles from "./Rail.module.css";

// The header on the reader surfaces. `Link` is a client-side navigation: the
// browser swaps the page's React tree instead of reloading the document, and
// prefetches the target when the link scrolls into view.
//
// The wordmark goes to `/` — the landing, which is what a wordmark means
// (demo-site.md §6.1) — and the working corpus is `/demo`.
//
// The rail carries the corpus size as a micro-label and one link out into the
// browsable corpus, and both come from `/api/meta`: the count is a fact about
// the corpus rather than about this search, and the link is hidden until the
// server says the route group is there, so a deployment running the dashboard
// off — or an edge rule that 404s it — leaves no invitation to a dead page
// (demo-site.md §6 item 1). A caller with no meta to hand renders neither.
export function Rail({ count, browse }: { count?: string | null; browse?: string | null } = {}) {
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
        <div className={styles.meta}>
          {count ? <span className={styles.count}>{count}</span> : null}
          {/* The accessible name says the whole thing whatever the rail has
              room to print: below the hand breakpoint the label is "browse →". */}
          {browse ? (
            <a className={styles.browse} href={browse} aria-label="Browse the corpus">
              browse<span className={styles.wide}> the corpus</span>{" "}
              <span aria-hidden="true">→</span>
            </a>
          ) : null}
        </div>
      </div>
    </header>
  );
}
