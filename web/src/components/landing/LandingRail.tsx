import { STATS } from "@/landing/corpus";
import { num } from "@/landing/format";
import styles from "./landing.module.css";

// The landing's own rail: it floats over the room rather than sitting on a
// page, and it carries the corpus readout instead of navigation. The wordmark
// is not a link here — this is the page it would point at. The Font-Logo Rule:
// the word, the gold full stop, nothing else.
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
