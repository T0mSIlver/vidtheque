import styles from "./page.module.css";

// Shown while the list streams in: the grid's shape, reserved, so the page
// does not jump when the rows land. Exported so the page can use the same
// skeleton as its Suspense fallback.
export function LibrarySkeleton() {
  return (
    <ul className={styles.grid} aria-busy="true" aria-label="Loading the library">
      {Array.from({ length: 12 }, (_, i) => (
        <li key={i} className={styles.ghost}>
          <span className={styles.ghostFrame} />
          <span className={styles.ghostLine} />
        </li>
      ))}
    </ul>
  );
}

export default function Loading() {
  return (
    <main className={styles.main}>
      <h1 className={styles.headline}>The library</h1>
      <LibrarySkeleton />
    </main>
  );
}
