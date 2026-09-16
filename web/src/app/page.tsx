import type { Metadata } from "next";
import { STATS } from "@/landing/corpus";
import { num, ymd } from "@/lib/format/landing";
import { BOOTH_QUESTION } from "@/landing/show";
import { BoothLog } from "@/components/landing/BoothLog";
import { CopyButton } from "@/components/landing/CopyButton";
import { HeroStage } from "@/components/landing/HeroStage";
import { LandingRail } from "@/components/landing/LandingRail";
import { Ledger } from "@/components/landing/Ledger";
import { Plates } from "@/components/landing/Plates";
import { WallBand } from "@/components/landing/WallBand";
import styles from "@/components/landing/landing.module.css";

// The landing (DESIGN.md's reference surface). Copy is bound by positioning.md;
// every figure comes from @/landing/corpus, and the page fetches nothing.

export const metadata: Metadata = {
  title: { absolute: "vidtheque — Builders talk. Your agent listens." },
  description: "Empowering AI with the knowledge of the builders and creators.",
  openGraph: {
    type: "website",
    siteName: "vidtheque",
    title: "vidtheque — Builders talk. Your agent listens.",
    description: "Empowering AI with the knowledge of the builders and creators.",
  },
  twitter: { card: "summary" },
};

export default function LandingPage() {
  return (
    <div className={styles.landing}>
      <LandingRail />

      <HeroStage />

      <section className={styles.beat} id="stills">
        <div className={styles.wrap}>
          <div className={styles.bhead}>
            <div className={styles.kick}>
              <s />
              <span className={`${styles.label} ${styles.gold}`}>four stills, off the wall</span>
            </div>
            <h2 className={styles.h2}>
              {"You don't have time to watch it all. "}
              <em>Your agent does.</em>
            </h2>
            <p className={styles.lede}>
              Every still below is a moment your agent can hand back mid-task: the sentence a
              builder actually said, the text the machine read off the screen behind them, and a
              link that lands on the second. When a moment has nothing to give, it <b>says so</b> —
              it never quietly narrows the answer.
            </p>
          </div>
          <div className={styles.legendrow}>
            <div className={styles.lg}>
              <i className={styles.hd} />
              <p>
                <b>heard</b> — every sentence spoken, aligned to the word.
              </p>
            </div>
            <div className={styles.lg}>
              <i className={styles.sn} />
              <p>
                <b>seen</b> — every line that crossed the screen, kept with the box it was read
                from.
              </p>
            </div>
            <div className={styles.lg}>
              <i className={styles.fr} />
              <p>
                <b>the frame</b> — kept as evidence, even when nothing on it is readable.
              </p>
            </div>
          </div>
          <Plates />
          <Ledger />
          <p className={`${styles.label} ${styles.ledgernote}`}>
            {num(STATS.cues)} sentences spoken · {num(STATS.words)} words · {num(STATS.ocr_frames)}{" "}
            frames read
          </p>
        </div>
      </section>

      <section className={`${styles.beat} ${styles.corpus}`} id="corpus">
        <div className={styles.wrap}>
          <div className={styles.bhead}>
            <div className={styles.kick}>
              <s />
              <span className={`${styles.label} ${styles.gold}`}>
                the wall, running · 70 keyframes
              </span>
            </div>
            <h2 className={styles.h2}>
              Follow the builders. Get the sentence, the slide, and <em>the second.</em>
            </h2>
            <p className={styles.lede}>
              Every frame below comes from a talk taken in whole. Point it at a conference, a
              channel, a creator, and their knowledge compounds into yours.
            </p>
          </div>
        </div>
        <WallBand />
        <div className={styles.wrap}>
          <div className={styles.bandmeta}>
            <span className={styles.label}>
              one channel followed so far — <span className={styles.gold}>{STATS.channel}</span>
            </span>
            <span className={`${styles.label} ${styles.hideS}`}>
              published {ymd(STATS.first_pub)} → {ymd(STATS.last_pub)} · click a frame to open the
              talk at that second
            </span>
          </div>
          <a className={styles.follow} href="#run">
            <span>
              + follow a channel
              <span className={styles.followsub}>
                today: point it at a channel. next: it keeps watching.
              </span>
            </span>
          </a>
        </div>
      </section>

      <section className={styles.beat} id="ask">
        <div className={styles.wrap}>
          <div className={styles.bhead}>
            <div className={styles.kick}>
              <s />
              <span className={`${styles.label} ${styles.gold}`}>
                the booth log · recorded 2026-08-09
              </span>
            </div>
            <h2 className={styles.h2}>
              Your agent <em>watched it.</em>
            </h2>
            <p className={styles.lede}>
              An agent, on the real protocol, answering a question no single talk answers —{" "}
              <b>with citations</b>, five speakers deep.
            </p>
          </div>

          <BoothLog
            question={BOOTH_QUESTION}
            head={
              <>
                <span className={`${styles.label} ${styles.gold}`}>21 calls</span>
                <span className={`${styles.label} ${styles.hideS}`}>claude sonnet</span>
              </>
            }
          >
            <p className={styles.call}>
              <span className={styles.k}>call</span> search{" "}
              <span className={styles.arg}>
                {'{"q":"LLM as a judge","content_type":"transcript","max_per_video":1,"limit":6}'}
              </span>
            </p>
            <p className={styles.ret}>← 6 moments · 6 talks · leg: transcript · relevance-first</p>

            <p className={styles.say}>
              They agree it is the default and disagree about whether it deserves to be.{" "}
              <b>Two positions, and a third group that ships it anyway:</b>
            </p>

            <div className={styles.qcite}>
              <p>
                {
                  "“And LLM as a judge doesn't really work either because LLMs don't have good taste in writing.”"
                }
              </p>
              <p className={styles.who2}>
                <b>Nick Heiner</b> · Surge AI · When Will The Benchmaxxing Plague End? · 16:01 ·{" "}
                <a href="https://youtu.be/-npY6XjM8CQ?t=961" rel="noopener">
                  youtu.be/-npY6XjM8CQ?t=961
                </a>
              </p>
            </div>
            <div className={styles.qcite}>
              <p>
                “Agent as a judge is about adaptive dynamic analysis. LLM as a judge just gives you
                a fixed rubric with these fixed scores.”
              </p>
              <p className={styles.who2}>
                <b>Aparna Dhinakaran</b> · Arize AI · The Future of Evals · 4:07 ·{" "}
                <a href="https://youtu.be/q2JrUKBMf0w?t=247" rel="noopener">
                  youtu.be/q2JrUKBMf0w?t=247
                </a>
              </p>
            </div>

            <p className={styles.call}>
              <span className={styles.k}>call</span> search{" "}
              <span className={styles.arg}>
                {
                  '{"q":"LLM as a judge in production","content_type":"transcript","max_per_video":1,"limit":6}'
                }
              </span>
            </p>
            <p className={styles.ret}>← 6 moments · 6 talks · leg: transcript</p>

            <p className={`${styles.say} ${styles.last}`}>
              And three teams who run it in production regardless — video at{" "}
              <span className={styles.id}>b_PmGocP4rc</span> (Character.ai), agent evals at{" "}
              <span className={styles.id}>31GUkCBD-Uc</span> (Uber), clinical at{" "}
              <span className={styles.id}>O72p-rBb2bA</span> (SonderMind). The disagreement is not
              “does it work”; it is <b>what it is allowed to be the judge of</b>.
            </p>
          </BoothLog>
        </div>
      </section>

      <section className={`${styles.beat} ${styles.run}`} id="run">
        <div className={styles.wrap}>
          <div className={styles.bhead}>
            <div className={styles.kick}>
              <s />
              <span className={`${styles.label} ${styles.gold}`}>yours, on your box</span>
            </div>
            <h2 className={styles.h2}>Run your own. Point your agent at it.</h2>
            <p className={styles.lede}>
              One compose file, one SQLite file, no build step, no runtime network dependency. It
              runs the CPU half on a Pi.
            </p>
          </div>
          <div className={styles.starts}>
            <div className={styles.start}>
              <span className={styles.label}>1 · run the corpus</span>
              <div className={styles.copybox}>
                <code>
                  <i>$</i> docker compose up -d
                </code>
                <CopyButton value="docker compose up -d" />
              </div>
              <p className={styles.startnote}>
                clone, copy <span className={styles.id}>.env.example</span> to{" "}
                <span className={styles.id}>.env</span>, and it is up.
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
            <span className={styles.label}>macOS · Linux · MIT · runs the CPU half on a Pi</span>
            <span className={`${styles.label} ${styles.r} ${styles.gold}`}>
              early development · no releases yet · schemas can still change
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
                The videos belong to the people who made them. vidtheque watched them, kept the
                timestamps, and sends you back to the source — every answer on this page is a
                quotation with a link that lands on the second it was said.
              </p>
              <p>
                Nothing is hosted or redistributed here: the file is downloaded, read, and deleted.
                What stays is the transcript, the text that was on screen, and the keyframes kept as
                evidence. A creator who would rather not be followed is a complete reason, and there
                is no appeal to make.
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
          <div className={styles.fbase}>
            <span className={`${styles.label} ${styles.r}`} />
          </div>
        </div>
      </footer>
    </div>
  );
}
