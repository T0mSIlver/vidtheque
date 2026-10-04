import Link from "next/link";
import { loadConsole } from "@/components/public/console/bootstrap";
import { Console } from "@/components/public/console/Console";
import { PAGE, SampleFeed } from "@/components/public/feed/SampleFeed";
import { readFeed } from "@/lib/api/search";
import { readEdition, TAG } from "./edition";
import styles from "./page.module.css";

// The one demo (demo-site.md §8): the sample feed, then Ask over the same
// corpus. `?edition=paris` narrows both to AI Engineer Paris, which is where
// `/paris` now lands (aie-paris-2026.md §4, amended 2026-10-04).

// Receipt-checked in `research/demo-queries-2026-08-10.md`; each pins the
// channel it needs, and no pin resets to all (demo-site.md §6.1).
const SEARCH_EXAMPLES = [
  { q: "context window costs money tokens", type: "ocr" },
  { q: "context engineering" },
  { q: "architecture diagram with boxes and arrows", type: "frame" },
  { q: "human annotation calibrate LLM judge", type: "transcript" },
] as const;

// `research/demo-queries-2026-08-13.md`, ordered by demo strength.
const ASK_EXAMPLES = [
  "Why does loop engineering look so much like building RLVR environments?",
  "How to do reinforcement learning when the task can't be verified?",
  "Does training on model-generated data compound quality or collapse it?",
  "Is the harness or the model more important?",
  "Why do agents write bad AGENTS.md?",
];

// Each one lands on a moment picked from the Paris transcripts, not on a topic.
const PARIS_SEARCH_EXAMPLES = [
  { q: "slop cannon" },
  { q: "meat proxy" },
  { q: "printer is on fire", type: "ocr" },
] as const;

const PARIS_ASK_EXAMPLES = [
  "Where do the speakers disagree about AI coding agents?",
  "Why did Opus 5 write tests that just restate the implementation?",
  "How can a video generation model control a robot arm?",
];

type Params = Record<string, string | string[] | undefined>;

function first(params: Params, key: string): string | undefined {
  const value = params[key];
  return Array.isArray(value) ? value[0] : value;
}

export default async function DemoPage({ searchParams }: PageProps<"/demo">) {
  const params = await searchParams;
  const paris = first(params, "edition") === "paris";
  const offset = Math.max(0, Math.min(1000, Number(first(params, "offset")) || 0));
  const tags = paris ? TAG : undefined;
  const base = paris ? "/demo?edition=paris" : "/demo";
  const [feed, bootstrap, edition] = await Promise.all([
    readFeed({ tags, limit: PAGE, offset }),
    loadConsole(searchParams, { tags }),
    paris ? readEdition() : null,
  ]);
  const page = (at: number) =>
    at ? `${base}${paris ? "&" : "?"}offset=${at}#feed` : `${base}#feed`;

  return (
    <main className={styles.main}>
      <div className={styles.hero}>
        <p className={styles.kick}>
          <s />
          <span>the sample feed · {paris ? "ai engineer paris 2026" : "ai engineer 2026"}</span>
        </p>
        <h1 className={styles.big}>
          Which talks will teach you something, <em>and which minutes.</em>
        </h1>
        <p className={styles.lede}>
          vidtheque watched every talk and judged each one for a sample reader: a builder shipping
          coding agents. Your own instance judges the channels you follow against what you care
          about.
        </p>
        <a className={styles.jump} href="#ask-head">
          Or ask the talks a question ↓
        </a>
      </div>

      <nav className={styles.filter} aria-label="Edition">
        <Link
          className={styles.filterLink}
          href="/demo#feed"
          aria-current={paris ? undefined : "page"}
        >
          All of AI Engineer 2026
        </Link>
        <Link
          className={styles.filterLink}
          href="/demo?edition=paris#feed"
          aria-current={paris ? "page" : undefined}
        >
          AI Engineer Paris
        </Link>
      </nav>

      <section id="feed" className={styles.feed} aria-label="Sample feed">
        <SampleFeed outcome={feed} offset={offset} page={page} />
      </section>

      <section className={styles.ask} aria-labelledby="ask-head">
        <h2 className={styles.askHead} id="ask-head">
          Ask the same talks
        </h2>
        <p className={styles.askLede}>
          Every answer quotes the talk and links to the second it was said.
        </p>
        <Console
          {...bootstrap}
          path={base}
          tags={tags}
          talks={edition?.kind === "ok" ? edition.page.talks : []}
          searchExamples={paris ? PARIS_SEARCH_EXAMPLES : SEARCH_EXAMPLES}
          askExamples={paris ? PARIS_ASK_EXAMPLES : ASK_EXAMPLES}
          noMatch={
            paris ? "Nothing in this edition matches this." : "Nothing in the corpus matches this."
          }
          showColdIntro={false}
        />
      </section>
    </main>
  );
}
