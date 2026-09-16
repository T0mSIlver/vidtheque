import { STATS } from "./data/corpus";
import { num } from "@/lib/format/landing";
import styles from "./landing.module.css";

// Floats over the hero; the wordmark is not a link on the page it would point at.
export function LandingRail() {
  return (
    <header className={styles.rail}>
      <div className={`${styles.wrap} ${styles.railin}`}>
        <div className={styles.mark}>
          <b>
            vidtheque<i>.</i>
          </b>
        </div>
        <div className={styles.railmeta}>
          <span className={`${styles.label} ${styles.hideS}`}>
            following <span className={`${styles.id} ${styles.gold}`}>{STATS.channel}</span> ·{" "}
            {num(STATS.talks)} talks watched
          </span>
        </div>
      </div>
    </header>
  );
}
