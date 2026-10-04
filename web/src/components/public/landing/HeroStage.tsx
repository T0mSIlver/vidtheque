/* eslint-disable @next/next/no-img-element */
import Link from "next/link";
import { WALL } from "./data/wall";
import styles from "./landing.module.css";

// Beat 1, the projection room: the wall of talks behind the lead sentence
// (positioning.md, amended 2026-10-04) and the page's two ways in.

// The tallest room: 12 columns × 16 rows; the room's overflow hides the rest.
const WALL_TILES = 192;
// A 1440 × 900 room shows 10 × 12; tiles past that wait for layout.
const EAGER_TILES = 120;

export function HeroStage() {
  return (
    <section className={`${styles.hero} ${styles.heroLead}`} id="top">
      <div className={styles.wallwrap} aria-hidden="true">
        <div className={styles.wall} data-hero="wall">
          {Array.from({ length: WALL_TILES }, (_, i) => {
            return (
              <div className={styles.wt} key={i}>
                <img
                  src={WALL[i % WALL.length]}
                  alt=""
                  decoding="async"
                  loading={i < EAGER_TILES ? undefined : "lazy"}
                />
              </div>
            );
          })}
        </div>
      </div>
      <div className={styles.scrim} aria-hidden="true" />

      <div className={`${styles.heroin} ${styles.heroinLead}`}>
        <div className={styles.herocopy}>
          <div className={styles.kick}>
            <s />
            <span className={`${styles.label} ${styles.gold}`}>follow the builders</span>
          </div>
          <h1 className={styles.h1}>
            vidtheque watches the channels you follow and tells you which videos,{" "}
            <em>and which minutes,</em> will teach you something.
          </h1>
          <p className={styles.lede}>
            Every recommendation says why, quotes the talk, and links to <b>the second</b> on
            YouTube. Your agent can read the same talks and cite them.
          </p>
          <div className={styles.ctarow}>
            <Link className={styles.cta} href="/demo">
              See the sample feed
              <svg
                viewBox="0 0 12 12"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.7"
                strokeLinecap="square"
                aria-hidden="true"
              >
                <path d="M2.6 6h6.6M6.4 3.2 9.2 6l-2.8 2.8" />
              </svg>
            </Link>
            <Link className={styles.ctaGhost} href="/docs">
              Run your own
            </Link>
          </div>
        </div>
      </div>
    </section>
  );
}
