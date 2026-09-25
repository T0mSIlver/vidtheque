import { Suspense } from "react";
import { ConnectSection } from "@/components/public/PublicShell";
import { loadConsole } from "@/components/public/console/bootstrap";
import { Console } from "@/components/public/console/Console";
import { readEdition, TAG } from "./edition";
import { Programme, ProgrammeLoading } from "./Programme";
import styles from "./page.module.css";

// Each one lands on a moment picked from the transcripts, not on a topic.
const SEARCH_EXAMPLES = [
  { q: "slop cannon" },
  { q: "meat proxy" },
  { q: "printer is on fire", type: "ocr" },
] as const;

const ASK_EXAMPLES = [
  "Where do the speakers disagree about AI coding agents?",
  "Why did Opus 5 write tests that just restate the implementation?",
  "How can a video generation model control a robot arm?",
];

// The console is awaited with the hero, so nothing swaps in above the fold;
// the programme streams in below it (aie-paris-2026.md §4).
export default async function ParisPage({ searchParams }: PageProps<"/paris">) {
  const [bootstrap, edition] = await Promise.all([
    loadConsole(searchParams, { tags: TAG }),
    readEdition(),
  ]);
  return (
    <main>
      <div className={styles.main}>
        <header className={styles.hero}>
          <p className={styles.kick}>
            <s />
            <span>ai engineer paris · 2026</span>
          </p>
          <h1 className={styles.big}>
            The main stage was eight and a half hours. Ask it a question.
          </h1>
          <p className={styles.lede}>
            Every main-stage talk from AI Engineer Paris, cited to the second, slides included.
            Point your own agent at it.
          </p>
        </header>
        <section aria-label="Search or ask the main-stage corpus">
          <Console
            {...bootstrap}
            path="/paris"
            tags={TAG}
            talks={edition.kind === "ok" ? edition.page.talks : []}
            searchExamples={SEARCH_EXAMPLES}
            askExamples={ASK_EXAMPLES}
            noMatch="Nothing in this edition matches this."
            showColdIntro={false}
          />
        </section>
      </div>
      <ConnectSection />
      <div className={styles.programme}>
        <Suspense fallback={<ProgrammeLoading />}>
          <Programme />
        </Suspense>
      </div>
    </main>
  );
}
