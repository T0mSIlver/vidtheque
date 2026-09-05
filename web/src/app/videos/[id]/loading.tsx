import styles from "./page.module.css";

export function DetailSkeleton() {
  return (
    <div className={styles.head} aria-busy="true" aria-label="Loading the video">
      <div className={styles.cover}>
        <span className={styles.ghostFrame} />
      </div>
      <div>
        <span className={styles.ghostLine} />
        <span className={styles.ghostTitle} />
      </div>
    </div>
  );
}

export default function Loading() {
  return (
    <main className={styles.main}>
      <DetailSkeleton />
    </main>
  );
}
