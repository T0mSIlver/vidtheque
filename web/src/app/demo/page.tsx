import { Suspense } from "react";
import { loadConsole } from "@/components/console/bootstrap";
import { Console } from "@/components/console/Console";
import { ConsoleReserve } from "@/components/console/Reserve";
import { CorpusPanel } from "./Corpus";
import styles from "./page.module.css";

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

export default function DemoPage({ searchParams }: PageProps<"/demo">) {
  return (
    <main className={styles.main}>
      <div className={styles.hero}>
        <p className={styles.kick}>
          <s />
          <span>the proof · ai engineer 2026</span>
        </p>
        <h1 className={styles.big}>
          The knowledge of AI Engineer 2026, on tap. <em>Ask it something.</em>
        </h1>
        <p className={styles.lede}>
          Your agent watched it — every sentence spoken, every line that crossed the screen, every
          frame. Every answer comes back with{" "}
          <b>the sentence, the slide, and the second it happened.</b>
        </p>
      </div>
      <Suspense fallback={<ConsoleReserve />}>
        <DemoConsole searchParams={searchParams} />
      </Suspense>
    </main>
  );
}

async function DemoConsole({ searchParams }: Pick<PageProps<"/demo">, "searchParams">) {
  const bootstrap = await loadConsole(searchParams);
  return (
    <Console
      {...bootstrap}
      path="/demo"
      searchExamples={SEARCH_EXAMPLES}
      askExamples={ASK_EXAMPLES}
      noMatch="Nothing in the corpus matches this."
      autoFocus
      corpus={
        <Suspense fallback={null}>
          <CorpusPanel />
        </Suspense>
      }
    />
  );
}
