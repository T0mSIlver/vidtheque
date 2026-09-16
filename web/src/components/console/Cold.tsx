import type { ContentType } from "@/lib/api/schemas";
import { InPlaceLink } from "./SearchResults";
import type { Mode } from "./url";
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

/**
 * Before the first search or question: teach the corpus. Both modes' panels
 * share one grid cell and the hidden one keeps its space, so switching modes
 * never moves what is below.
 */
export function Cold({
  mode,
  boot,
  searchExamples,
  searchPrompt,
  askExamples,
  corpus,
  href,
  onSearchExample,
  onAskExample,
}: {
  mode: Mode;
  boot: Boot;
  searchExamples: readonly SearchExample[];
  searchPrompt?: string;
  askExamples: readonly string[];
  corpus?: React.ReactNode;
  href: (q: string, type: ContentType) => string;
  onSearchExample: (example: SearchExample) => void;
  onAskExample: (question: string) => void;
}) {
  const search = mode === "search";
  return (
    <section className={styles.empty} aria-label="Getting started">
      <BootNotice boot={boot} />
      <p className={styles.kick}>
        <s />
        <span>start here</span>
      </p>
      <div className={styles.stack}>
        <div className={search ? undefined : styles.off} inert={!search}>
          <h2 className={styles.exhead}>
            {searchExamples.length ? "Try one of these" : "Search by keyword"}
          </h2>
          <p className={styles.exnote}>
            {searchPrompt ??
              "Keyword search over every sentence spoken, every line that crossed the screen, and the frames themselves."}
          </p>
          {searchExamples.length ? (
            <ul className={styles.examples}>
              {searchExamples.map((example) => (
                <li key={example.q}>
                  {/* The text is the query, so a result reads back against it. */}
                  <InPlaceLink
                    className={styles.example}
                    href={href(example.q, example.type ?? "all")}
                    onRun={() => onSearchExample(example)}
                  >
                    {example.q}
                  </InPlaceLink>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
        <div className={search ? styles.off : undefined} inert={search}>
          <h2 className={styles.exhead}>Ask one of these</h2>
          {/* A question box does not teach itself: say the answer is read out
              of the talks, not invented over them. */}
          <p className={styles.exnote}>
            None of these is answered by one talk. The model reads the corpus to build the answer
            and hands back the sentence, the talk and the second it was said.
          </p>
          <ul className={styles.examples}>
            {askExamples.map((question) => (
              <li key={question}>
                <button
                  type="button"
                  className={styles.example}
                  onClick={() => onAskExample(question)}
                >
                  {question}
                </button>
              </li>
            ))}
          </ul>
        </div>
      </div>
      {corpus}
    </section>
  );
}
