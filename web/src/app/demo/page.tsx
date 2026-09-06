import Link from "next/link";
import { Suspense } from "react";
import { AskMode } from "@/components/AskMode";
import { RetryIn } from "@/components/RetryIn";
import type { MachineState } from "@/components/SearchBox";
import { ContentType, type ContentType as Channel, type Video } from "@/lib/api/schemas";
import { groupByVideo } from "@/lib/group";
import { readCorpus, readMeta, searchCorpus, type SearchOutcome } from "@/lib/search";
import { Query } from "./Query";
import { Results } from "./Results";
import styles from "./page.module.css";

// The demo — the working corpus, at `/demo` (demo-site.md §1). The hero and the
// examples are copy and live here; the box, the skeleton and "More results" are
// the page's one client boundary; the results are a Server Component that
// re-renders for each URL.

// SEARCH — keywords stay keywords. The four receipt-checked pairs from
// `research/demo-queries-2026-08-10.md`, plus the frame-leg example carried
// over from the 2026-08-09 harvest (the 08-10 file is additive and has no
// visual query in it). Each pins the channel it needs, because a query that
// can only be answered by one leg proves nothing buried under the others; a
// pin with no `type` is a reset to `all`, which is what stops an on-screen
// example followed by a spoken one from running the second against OCR and
// reporting an empty corpus.
//
// There is no fifth. It used to be "FlashAttention-4", the query the corpus
// refuses — the right example for a *report*, and on the page a button whose
// whole payload is the word "nothing" (Tom, 2026-08-11). Every example here
// returns evidence; the refusal path is still one typed query away.
const SEARCH_EXAMPLES: { q: string; type?: Channel }[] = [
  { q: "context window costs money tokens", type: "ocr" },
  { q: "context engineering" },
  { q: "architecture diagram with boxes and arrows", type: "frame" },
  { q: "human annotation calibrate LLM judge", type: "transcript" },
];

// ASK — hard questions with no obvious answer, in the words a person would
// use, each receipt-checked against the live corpus in
// `research/demo-queries-2026-08-13.md` and each on a different axis: agent
// loops, post-training, data, harnesses, docs-for-agents. Ordered by demo
// strength, so the flagship is first and the purest synthesis test is last.
export const ASK_EXAMPLES = [
  "Why does loop engineering look so much like building RLVR environments?",
  "How to do reinforcement learning when the task can't be verified?",
  "Does training on model-generated data compound quality or collapse it?",
  "Is the harness or the model more important?",
  "Why do agents write bad AGENTS.md?",
];

const CHANNEL_NAME: Record<string, string> = {
  transcript: "the transcript",
  ocr: "on-screen text",
  frame: "frames",
};

export default function DemoPage(props: PageProps<"/demo">) {
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
      <Suspense fallback={<Booting />}>
        <Mode searchParams={props.searchParams} />
      </Suspense>
    </main>
  );
}

// Ask is the default mode (Tom, 2026-08-11; demo-site.md §6.1): the headline
// says "ask it something", so the box under it had better be the one that
// answers a question. Exactly three things override it, and each is explicit.
function wantsAsk(ask: string | undefined, q: string, askEnabled: boolean): boolean {
  // A deployment with no key configured — the one load that swaps the mode on
  // screen, and it is the misconfiguration rather than the demo.
  if (!askEnabled) return false;
  if (ask === "0") return false;
  if (ask === "1") return true;
  // `?q=` with no `ask=` at all is search: that is what every link written
  // before ask was the default looks like, and what the box writes for a
  // visitor who never touched the switch (§6.2).
  return !q;
}

// Exported for its test: an async Server Component inside a `<Suspense>` is
// not something a test renderer can await, and this is the whole of the page's
// own logic — which mode, which state word, which screen.
export async function Mode({ searchParams }: Pick<PageProps<"/demo">, "searchParams">) {
  const sp = await searchParams;
  const boot = await readMeta();
  const askEnabled = boot.kind === "ok" && boot.meta.ask_enabled;
  const q = String(sp.q ?? "").trim();
  const type = ContentType.catch("all").parse(sp.type ?? "all");
  const offset = Math.max(0, Number.parseInt(String(sp.offset ?? "0"), 10) || 0);

  // A loaded question never fires itself: an answer costs a slice of the daily
  // model budget, and neither a shared link nor a crawler may spend it.
  if (wantsAsk(sp.ask ? String(sp.ask) : undefined, q, askEnabled)) {
    return (
      <AskMode initialQ={q} examples={ASK_EXAMPLES}>
        <Corpus videos={await readCorpus()} />
      </AskMode>
    );
  }

  const outcome = q ? await searchCorpus({ q, type, offset }) : null;
  const groups = outcome?.kind === "ok" ? groupByVideo(outcome.page.results) : [];
  return (
    <Query
      state={outcome ? stateOf(outcome) : bootState(boot.kind)}
      askEnabled={askEnabled}
      shape={groups.map((group) => group.hits.length)}
    >
      {q ? (
        <Outcome q={q} type={type} outcome={outcome as SearchOutcome} />
      ) : (
        <Cold boot={boot.kind} videos={await readCorpus()} />
      )}
    </Query>
  );
}

// The machine's own word for how the search ended.
function stateOf(outcome: SearchOutcome): MachineState {
  if (outcome.kind === "rate_limited") return "refused";
  if (outcome.kind === "refused") return "refused";
  if (outcome.kind === "unreachable") return "no reply";
  return outcome.page.results.length ? "ready" : "no hits";
}

// …and, before a search, for how the boot call ended. Rate-limited and
// unreachable are different facts: one is over in a minute.
function bootState(kind: "ok" | "rate_limited" | "unreachable"): MachineState {
  if (kind === "rate_limited") return "rate limited";
  if (kind === "unreachable") return "no reply";
  return "ready";
}

function Outcome({ q, type, outcome }: { q: string; type: Channel; outcome: SearchOutcome }) {
  if (outcome.kind === "rate_limited") {
    return (
      <div className={styles.foot}>
        <RetryIn seconds={outcome.retryAfter} variant="notice" />
      </div>
    );
  }
  // The facade's own sentence, and the step it named. An error boundary would
  // replace both with one generic line, which is the only account a visitor
  // gets of why (demo-site.md §6.1).
  if (outcome.kind === "refused" || outcome.kind === "unreachable") {
    const message = outcome.kind === "refused" ? outcome.message : "Could not reach the server.";
    const next = outcome.kind === "refused" ? outcome.next : undefined;
    return (
      <div className={styles.foot}>
        <div className={`${styles.notice} ${styles.noticeBad}`}>
          <p className={styles.noticeTitle}>{message}</p>
          {next ? <p className={styles.noticeDetail}>{next}</p> : null}
        </div>
      </div>
    );
  }

  const { results, pagination, notes, data_status } = outcome.page;
  if (results.length === 0) return <NoResults q={q} type={type} dataStatus={data_status} />;
  return <Results q={q} type={type} hits={results} pagination={pagination} notes={notes} />;
}

// Nothing matched: one short sentence and the way back to something that
// works. The query is quoted back two centimetres under the box that still
// holds it, so it is not repeated here, and "try fewer words" is a tip nobody
// takes phrased as though the visitor had made a mistake (Tom, 2026-08-11).
function NoResults({
  q,
  type,
  dataStatus,
}: {
  q: string;
  type: Channel;
  dataStatus: string | null;
}) {
  // "Nothing matched" is a lie when there is nothing to match: an instance that
  // has not indexed anything yet says so instead of blaming the query.
  if (dataStatus === "empty") {
    return (
      <div className={styles.notice}>
        <p className={styles.noticeTitle}>Nothing is indexed yet.</p>
      </div>
    );
  }
  return (
    <div className={styles.notice}>
      <p className={styles.noticeTitle}>Nothing in the corpus matches this.</p>
      <ul className={styles.tips}>
        {/* Not advice but the visitor's own filter: leaving someone inside
            "frames only" is how a demo looks broken. */}
        {type !== "all" ? (
          <li>
            {CHANNEL_NAME[type]} only ·{" "}
            <Link className={styles.linky} href={`/demo?ask=0&q=${encodeURIComponent(q)}`}>
              Search all
            </Link>
          </li>
        ) : null}
        <li>
          Try{" "}
          {/* Clears the box, resets the channel and puts the cold page back —
              the box takes the caret when the query it held goes. */}
          <Link className={styles.linky} href="/demo?ask=0">
            one of the examples
          </Link>
          .
        </li>
      </ul>
    </div>
  );
}

// Before the first search: teach the corpus rather than show a blank page.
function Cold({ boot, videos }: { boot: "ok" | "rate_limited" | "unreachable"; videos: Video[] }) {
  return (
    <>
      {boot !== "ok" ? (
        <p className={styles.status} role="status">
          {boot === "rate_limited"
            ? "too many requests — this page loads again in a minute"
            : "could not reach the server"}
        </p>
      ) : null}
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
          {SEARCH_EXAMPLES.map((example) => (
            <li key={example.q}>
              {/* The button's own text *is* the query, so a visitor can read
                  the result back against the words that were sent. */}
              <Link className={styles.example} href={hrefFor(example)}>
                {example.q}
              </Link>
            </li>
          ))}
        </ul>
        <Corpus videos={videos} />
      </section>
    </>
  );
}

function hrefFor(example: { q: string; type?: Channel }): string {
  const params = new URLSearchParams({ ask: "0", q: example.q });
  if (example.type) params.set("type", example.type);
  return `/demo?${params}`;
}

// What is actually in here, listed on the cold page — the fastest way to
// understand a corpus is to see the talks it is made of.
function Corpus({ videos }: { videos: Video[] }) {
  if (!videos.length) return null;
  return (
    <div className={styles.corpus}>
      <p className={styles.phead}>in this corpus</p>
      <ul>
        {videos.map((video: Video) => (
          <li key={video.video_id}>
            <a href={video.link} target="_blank" rel="noopener noreferrer">
              {video.title}
            </a>
            {video.channel ? <span className={styles.who}>{video.channel}</span> : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

// The first paint, before the boot call has landed. The box's own shape, so the
// page does not grow one when it arrives.
function Booting() {
  return <div className={styles.booting} aria-hidden="true" />;
}
