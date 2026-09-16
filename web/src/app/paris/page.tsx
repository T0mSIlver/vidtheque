import { Suspense } from "react";
import { loadConsole } from "@/components/console/bootstrap";
import { Console } from "@/components/console/Console";
import { ConsoleReserve } from "@/components/console/Reserve";
import { readEdition, TAG } from "./edition";
import { Programme, ProgrammeLoading } from "./Programme";
import styles from "./page.module.css";

const ASK_EXAMPLES = [
  "What did the main stage say about running agents in production?",
  "Where do the speakers disagree about inference infrastructure?",
  "What practical advice appeared on the slides?",
];

// The console comes straight after the hero; the programme streams in below
// it, so its height never moves the box (aie-paris-2026.md §4).
export default function ParisPage({ searchParams }: PageProps<"/paris">) {
  return (
    <main className={styles.main}>
      <header className={styles.hero}>
        <p className={styles.kick}>
          <s />
          <span>ai engineer paris · 2026</span>
        </p>
        <h1 className={styles.big}>
          The main stage was eight and a half hours. Ask it a question.
        </h1>
        <p className={styles.lede}>
          Every main-stage talk from AI Engineer Paris, cited to the second, slides included. Point
          your own agent at it.
        </p>
        <p className={styles.credit}>Organized by Mistral.</p>
      </header>
      <section aria-labelledby="query-title">
        <p className={styles.kick}>
          <s />
          <span>search or ask</span>
        </p>
        <h2 id="query-title" className={styles.sectionTitle}>
          Find a moment in the main-stage corpus
        </h2>
        <Suspense fallback={<ConsoleReserve />}>
          <ParisConsole searchParams={searchParams} />
        </Suspense>
      </section>
      <div className={styles.programme}>
        <Suspense fallback={<ProgrammeLoading />}>
          <Programme />
        </Suspense>
      </div>
    </main>
  );
}

async function ParisConsole({ searchParams }: Pick<PageProps<"/paris">, "searchParams">) {
  const [bootstrap, edition] = await Promise.all([
    loadConsole(searchParams, { tags: TAG }),
    readEdition(),
  ]);
  return (
    <Console
      {...bootstrap}
      path="/paris"
      tags={TAG}
      talks={edition.kind === "ok" ? edition.page.talks : []}
      searchPrompt="Search every spoken sentence, slide and frame in this edition."
      askExamples={ASK_EXAMPLES}
      noMatch="Nothing in this edition matches this."
    />
  );
}
