/* eslint-disable @next/next/no-img-element */
import Link from "next/link";
import { ASSETS, GRID_BY_VID } from "@/landing/corpus";
import { QUERIES, WALL_ORDER, type CannedQuery } from "@/landing/show";
import { EvidenceFrame } from "./EvidenceFrame";
import { HeroController, type HeroCue } from "./HeroController";
import { Receipt } from "./Receipt";
import styles from "./landing.module.css";

// Beat 1, the projection room. Everything it can show is rendered here: the
// wall, the chips and each answer the light table can hold. HeroController
// only measures and toggles, so nothing appears or moves at hydration.

// Enough tiles for a tall stacked hero; CSS sizes them and the controller
// hides the rows below the room.
const WALL_TILES = 288;

const CUES: HeroCue[] = QUERIES.map((q) => ({
  q: q.q,
  vid: q.vid,
  at: q.at,
  still: ASSETS + q.img,
  cover: ASSETS + GRID_BY_VID[q.vid].img,
}));

export function HeroStage() {
  return (
    <HeroController className={styles.hero} id="top" cues={CUES}>
      <div className={styles.wallwrap} aria-hidden="true" data-hero="wallwrap">
        <div className={styles.wall} data-hero="wall">
          {Array.from({ length: WALL_TILES }, (_, i) => {
            const g = WALL_ORDER[i % WALL_ORDER.length];
            return (
              <div className={styles.wt} data-vid={g.vid} key={i}>
                <img src={ASSETS + g.img} alt="" decoding="async" />
              </div>
            );
          })}
        </div>
      </div>
      <div className={styles.scrim} aria-hidden="true" />

      <div className={styles.heroin}>
        <div className={styles.herocopy}>
          <div className={styles.kick} data-hero="obstacle-text">
            <s />
            <span className={`${styles.label} ${styles.gold}`}>
              the knowledge of the builders, on tap
            </span>
          </div>
          <h1 className={styles.h1} data-hero="obstacle-text">
            Builders talk.
            <br />
            Your agent <em>listens.</em>
          </h1>
          <p className={styles.lede} data-hero="obstacle-text">
            Behind this page is every talk AI Engineer published in 2026, more conference than
            anyone has time for. Ask any AI engineering question, your agent answers from what was
            spoken and what was shown, <b>down to the second.</b>
          </p>
          <div className={styles.slug} data-hero="slug">
            <span className={styles.ic}>
              <svg viewBox="0 0 12 12" aria-hidden="true">
                <path
                  d="M3.6 1.8 L8.2 6 L3.6 10.2"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.6"
                />
              </svg>
            </span>
            <span className={styles.q}>
              <span data-hero="qtext" />
              <span className={styles.caret} />
            </span>
            <span className={styles.st} data-s="ready" data-hero="status">
              ready
            </span>
          </div>
          <div className={styles.ctarow}>
            {/* No prefetch: the landing makes no network requests of its own. */}
            <Link className={styles.cta} href="/demo" prefetch={false} data-hero="obstacle">
              Open the demo
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
            <div
              className={styles.chips}
              data-hero="chips"
              role="group"
              aria-label="Example questions"
            >
              {QUERIES.map((q, i) => (
                <button type="button" data-i={i} key={q.label}>
                  {q.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* `blank` keeps the first answer's height under the empty table, so
            the first lift does not resize the room. */}
        <div
          className={`${styles.bench} ${styles.idle} ${styles.blank}`}
          data-hero="bench"
          aria-live="polite"
        >
          <div className={`${styles.benchhead} ${styles.blankhead}`}>
            <span className={`${styles.label} ${styles.gold}`}>the light table</span>
            <span className={`${styles.label} ${styles.r}`}>nothing lifted yet</span>
          </div>
          {QUERIES.map((q, i) => (
            <div className={styles.panel} data-panel={i} hidden={i !== 0} key={q.label}>
              <BenchPanel q={q} />
            </div>
          ))}
        </div>
      </div>

      <div className={styles.herofoot} data-hero="obstacle">
        <div className={`${styles.wrap} ${styles.in}`}>
          <span className={styles.label}>the videos belong to the people who made them</span>
        </div>
      </div>
    </HeroController>
  );
}

function BenchPanel({ q }: { q: CannedQuery }) {
  return (
    <>
      <div className={styles.benchhead}>
        <span className={`${styles.label} ${styles.gold}`}>the light table</span>
        <span className={`${styles.label} ${styles.seen}`}>{`seen — ${q.seen}`}</span>
        <span className={`${styles.label} ${styles.r} ${styles.hideS}`}>
          src <span className={styles.id}>{q.vid}</span> · tc {q.tc}
        </span>
      </div>
      <EvidenceFrame
        src={ASSETS + q.img}
        alt={`Matched keyframe at ${q.at} of ${q.talk} — ${q.who}`}
        boxes={q.boxes}
        loading="lazy"
      />
      <div className={styles.benchsay}>
        <span className={`${styles.label} ${styles.gold}`}>{`heard — spoken at ${q.saidTc}`}</span>
        <p className={styles.said}>{q.said}</p>
        <div className={styles.who}>
          <strong>{q.who}</strong>
          <em>{q.talk}</em>
        </div>
      </div>
      <div className={styles.benchfoot}>
        <Receipt videoId={q.vid} t={q.t} />
        <span className={`${styles.label} ${styles.r} ${styles.hideS}`}>
          {`${q.mode} · ${q.counts}`}
        </span>
      </div>
    </>
  );
}
