import Link from "next/link";
import { Recommendation } from "@/components/public/feed/Recommendation";
import type { FeedOutcome } from "@/lib/api/search";
import { CopyButton } from "./CopyButton";
import { HeroStage } from "./HeroStage";
import styles from "./landing.module.css";

// The landing (DESIGN.md's reference surface). Copy is bound by positioning.md
// as amended 2026-10-04: the lead sentence, three sample recommendations read
// live from `/api/feed`, then the two ways in.

export function Landing({ feed }: { feed: FeedOutcome }) {
  const picks = feed.kind === "ok" ? feed.feed.items.slice(0, 3) : [];
  const profile = feed.kind === "ok" ? feed.feed.profile : null;
  return (
    <div className={styles.landing}>
      <HeroStage />

      {picks.length ? (
        <section className={styles.beat} id="picks">
          <div className={styles.wrap}>
            <div className={styles.bhead}>
              <div className={styles.kick}>
                <s />
                <span className={`${styles.label} ${styles.gold}`}>three from the sample feed</span>
              </div>
              <h2 className={styles.h2}>
                What it picked for <em>{profile?.name.toLowerCase() ?? "a sample reader"}.</em>
              </h2>
              <p className={styles.lede}>
                Judged from AI Engineer 2026&apos;s talks. Your own instance judges the channels you
                follow against what you care about.
              </p>
            </div>
            <ol className={styles.picks}>
              {picks.map((item) => (
                <li key={item.video_id}>
                  <Recommendation item={item} compact />
                </li>
              ))}
            </ol>
            <Link className={styles.more} href="/demo">
              The whole sample feed →
            </Link>
          </div>
        </section>
      ) : null}

      <section className={`${styles.beat} ${styles.run}`} id="run">
        <div className={styles.wrap}>
          <div className={styles.bhead}>
            <div className={styles.kick}>
              <s />
              <span className={`${styles.label} ${styles.gold}`}>yours, on your box</span>
            </div>
            <h2 className={styles.h2}>Run your own. Point your agent at it.</h2>
            <p className={styles.lede}>
              One compose file and one SQLite file on a machine at home. It follows your channels,
              learns what you care about, and pushes the videos worth your time to your phone.
            </p>
          </div>
          <div className={styles.starts}>
            <div className={styles.start}>
              <span className={styles.label}>1 · run it</span>
              <div className={styles.copybox}>
                <code>
                  <i>$</i> docker compose up -d
                </code>
                <CopyButton value="docker compose up -d" />
              </div>
              <p className={styles.startnote}>
                clone, copy <span className={styles.id}>.env.example</span> to{" "}
                <span className={styles.id}>.env</span>, and it is up.{" "}
                <Link className={styles.lnk} href="/docs">
                  The setup guide
                </Link>
              </p>
            </div>
            <div className={styles.start}>
              <span className={styles.label}>2 · hand it to your agent</span>
              <div className={styles.copybox}>
                <code>
                  <i>$</i> claude mcp add --transport http vidtheque https://vidtheque.dev/mcp
                </code>
                <CopyButton value="claude mcp add --transport http vidtheque https://vidtheque.dev/mcp" />
              </div>
              <p className={styles.startnote}>
                one line for Claude, Codex, or anything that speaks the protocol.
              </p>
            </div>
          </div>
          <div className={styles.fbase}>
            <span className={styles.label}>
              Linux · MIT ·{" "}
              <Link className={styles.lnk} href="/app">
                Android app
              </Link>
            </span>
            <span className={`${styles.label} ${styles.r} ${styles.gold}`}>
              early development · schemas can still change
            </span>
          </div>
        </div>
      </section>

      <footer className={styles.footer}>
        <div className={styles.wrap}>
          <div className={styles.fgrid}>
            <div>
              <span className={`${styles.label} ${styles.gold}`}>attribution</span>
              <p>
                The videos belong to the people who made them. vidtheque quotes them briefly, names
                the talk, and links to the second it was said.
              </p>
              <p>
                Nothing is hosted or redistributed here: the file is downloaded, read, and deleted.
                What stays is the transcript, the text that was on screen, and the keyframes kept as
                evidence. A creator who would rather not be followed is a complete reason, and there
                is no appeal to make.{" "}
                <a
                  className={styles.lnk}
                  href="https://github.com/T0mSIlver/vidtheque/blob/main/docs/takedown.md"
                  rel="noopener"
                >
                  Removal on request
                </a>
                .
              </p>
              <p>
                <a
                  className={styles.lnk}
                  href="https://github.com/T0mSIlver/vidtheque"
                  rel="noopener"
                >
                  the repo
                </a>{" "}
                · MIT.
              </p>
            </div>
            <div className={styles.fmark}>
              <b className={styles.fmarkWord}>
                vidtheque<i>.</i>
              </b>
            </div>
          </div>
        </div>
      </footer>
    </div>
  );
}
