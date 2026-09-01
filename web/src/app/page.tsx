import Link from "next/link";
import { Suspense } from "react";
import { ResultGroup } from "@/components/ResultGroup";
import { RetryIn } from "@/components/RetryIn";
import { SearchBox } from "@/components/SearchBox";
import { ContentType } from "@/lib/api";
import { groupByVideo } from "@/lib/group";
import { searchCorpus, SEARCH_PAGE } from "@/lib/search";
import styles from "./page.module.css";

const EXAMPLES = ["kv cache", "context engineering", "MCP servers", "evals in production"];

// The search page. Static shell; the box is the one Client Component and the
// results are a Server Component that re-renders for each URL.
export default function SearchPage(props: PageProps<"/">) {
  return (
    <main className={styles.main}>
      <h1 className={styles.headline}>Ask the talks.</h1>
      {/* useSearchParams inside SearchBox needs a Suspense boundary of its own. */}
      <Suspense fallback={null}>
        <SearchBox />
      </Suspense>
      <Suspense fallback={<ResultsSkeleton />}>
        <Results searchParams={props.searchParams} />
      </Suspense>
    </main>
  );
}

async function Results({ searchParams }: Pick<PageProps<"/">, "searchParams">) {
  const sp = await searchParams;
  const q = String(sp.q ?? "").trim();
  const type = ContentType.catch("all").parse(sp.type ?? "all");
  const offset = Math.max(0, Number.parseInt(String(sp.offset ?? "0"), 10) || 0);

  if (!q) return <Examples />;

  const outcome = await searchCorpus({ q, type, offset });
  if (outcome.kind === "rate_limited") return <RetryIn seconds={outcome.retryAfter} />;

  const { results, pagination, notes, data_status } = outcome.page;
  if (results.length === 0) {
    return (
      <div className={styles.empty}>
        <p>
          {data_status === "empty"
            ? "Nothing is indexed in this corpus yet."
            : "Nothing in the corpus matches this."}
        </p>
        {type !== "all" ? (
          <p>
            <Link href={`/?q=${encodeURIComponent(q)}`}>Search all</Link>
          </p>
        ) : null}
        <p>
          <Link href="/">Try one of the examples.</Link>
        </p>
      </div>
    );
  }

  const groups = groupByVideo(results);
  const query = new URLSearchParams({ q, ...(type !== "all" ? { type } : {}) });
  return (
    <section className={styles.results} aria-label="Results">
      <p className={styles.count}>
        {offset + 1}–{offset + results.length}
        {pagination.approx_total != null ? ` of about ${pagination.approx_total}` : ""} · {groups.length}{" "}
        {groups.length === 1 ? "talk" : "talks"}
      </p>
      {notes.map((note) => (
        <p key={note} className={styles.note}>
          {note}
        </p>
      ))}
      <div className={styles.cards}>
        {groups.map((g) => (
          <ResultGroup key={g.video_id} group={g} />
        ))}
      </div>
      <nav className={styles.pager} aria-label="Pages">
        {offset > 0 ? (
          <Link href={`/?${query}&offset=${Math.max(0, offset - SEARCH_PAGE)}`}>← previous</Link>
        ) : (
          <span />
        )}
        {pagination.has_more ? (
          <Link href={`/?${query}&offset=${offset + SEARCH_PAGE}`}>more results →</Link>
        ) : null}
      </nav>
    </section>
  );
}

function Examples() {
  return (
    <div className={styles.examples}>
      <p className={styles.label}>Try</p>
      <ul className={styles.chips}>
        {EXAMPLES.map((q) => (
          <li key={q}>
            <Link href={`/?q=${encodeURIComponent(q)}`} className={styles.chip}>
              {q}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ResultsSkeleton() {
  return (
    <div className={styles.results} aria-busy="true" aria-label="Searching">
      {Array.from({ length: 3 }, (_, i) => (
        <div key={i} className={styles.ghost}>
          <span className={styles.ghostHead} />
          <span className={styles.ghostLine} />
          <span className={styles.ghostLine} />
        </div>
      ))}
    </div>
  );
}
