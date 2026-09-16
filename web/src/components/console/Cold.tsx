import type { ContentType } from "@/lib/api/schemas";
import { InPlaceLink } from "./SearchResults";
import styles from "./console.module.css";

export interface SearchExample {
  q: string;
  /** No `type` resets the chips to all (demo-site.md §6.1). */
  type?: ContentType;
}

export type Boot = "ok" | "rate_limited" | "unreachable";

// `/api/meta` shares the search bucket: a spent bucket and a dead server are
// different facts, and neither may boot into a page of `undefined`.
function BootNotice({ boot }: { boot: Boot }) {
  if (boot === "ok") return null;
  return (
    <p className={styles.status} role="status">
      {boot === "rate_limited"
        ? "too many requests — this page loads again in a minute"
        : "could not reach the server"}
    </p>
  );
}

/** Before the first search: teach the corpus rather than show a blank page. */
export function SearchCold({
  boot,
  examples,
  prompt,
  corpus,
  href,
  onExample,
}: {
  boot: Boot;
  examples: readonly SearchExample[];
  prompt?: string;
  corpus?: React.ReactNode;
  href: (q: string, type: ContentType) => string;
  onExample: (example: SearchExample) => void;
}) {
  return (
    <>
      <BootNotice boot={boot} />
      {prompt ? <p className={styles.prompt}>{prompt}</p> : null}
      {examples.length ? (
        <section className={styles.empty} aria-label="Getting started">
          <p className={styles.kick}>
            <s />
            <span>start here</span>
          </p>
          <h2 className={styles.exhead}>Try one of these</h2>
          <p className={styles.exnote}>
            Keyword search over every sentence spoken, every line that crossed the screen, and the
            frames themselves.
          </p>
          <ul className={styles.examples}>
            {examples.map((example) => (
              <li key={example.q}>
                {/* The text is the query, so a result reads back against it. */}
                <InPlaceLink
                  className={styles.example}
                  href={href(example.q, example.type ?? "all")}
                  onRun={() => onExample(example)}
                >
                  {example.q}
                </InPlaceLink>
              </li>
            ))}
          </ul>
          {corpus}
        </section>
      ) : null}
    </>
  );
}

export function AskCold({
  examples,
  corpus,
  onExample,
}: {
  examples: readonly string[];
  corpus?: React.ReactNode;
  onExample: (question: string) => void;
}) {
  return (
    <section className={styles.empty} aria-label="Getting started">
      <p className={styles.kick}>
        <s />
        <span>start here</span>
      </p>
      <h2 className={styles.exhead}>Ask one of these</h2>
      {/* A question box does not teach itself: say the answer is read out of
          the talks, not invented over them. */}
      <p className={styles.exnote}>
        None of these is answered by one talk. The model reads the corpus to build the answer and
        hands back the sentence, the talk and the second it was said.
      </p>
      <ul className={styles.examples}>
        {examples.map((question) => (
          <li key={question}>
            <button type="button" className={styles.example} onClick={() => onExample(question)}>
              {question}
            </button>
          </li>
        ))}
      </ul>
      {corpus}
    </section>
  );
}
