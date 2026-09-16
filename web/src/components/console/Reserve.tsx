import styles from "./console.module.css";

/** The bar and its controls' footprint while the bootstrap read is out. */
export function ConsoleReserve() {
  return (
    <div className={styles.reserve} aria-busy="true" aria-label="Loading search">
      <div className={styles.bar} />
    </div>
  );
}
