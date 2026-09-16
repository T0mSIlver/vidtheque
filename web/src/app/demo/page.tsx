import Link from "next/link";
import { Suspense } from "react";
import { AskMode } from "@/components/AskMode";
import { RetryIn } from "@/components/RetryIn";
import type { MachineState } from "@/components/SearchBox";
import { ContentType, type ContentType as Channel, type Video } from "@/lib/api/schemas";
import { groupByVideo } from "@/lib/group";
import { readCorpus, readMeta, searchCorpus, type SearchOutcome } from "@/lib/search";
import type { MetaOutcome } from "@/lib/api/meta";
import { DEFAULT_SHAPE, Query, Skeleton } from "./Query";
import { Results } from "./Results";
import { TryAgain } from "./TryAgain";
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

type Params = Awaited<PageProps<"/demo">["searchParams"]>;

export default async function DemoPage(props: PageProps<"/demo">) {
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
      {/* The only `await` above the box, and it touches no network: which
          screen this is, is in the URL. */}
      <Console params={await props.searchParams} />
    </main>
  );
}

// Ask is the default mode (Tom, 2026-08-11; demo-site.md §6.1): the headline
// says "ask it something", so the box under it had better be the one that
// answers a question. Exactly two things override it, and each is explicit.
//
// The third — a deployment with no key configured — is *not* read here, because
// reading it means waiting on `/api/meta` to find out which box to draw. The
// markup states the default, as `demo/index.html` did, and `AskSwitch` corrects
// it when the boot call lands: that swap is rare and it is the misconfiguration
// rather than the demo.
function wantsAsk(ask: string | undefined, q: string): boolean {
  if (ask === "0") return false;
  if (ask === "1") return true;
  // `?q=` with no `ask=` at all is search: that is what every link written
  // before ask was the default looks like, and what the box writes for a
  // visitor who never touched the switch (§6.2).
  return !q;
}

/**
 * Everything under the hero — and **nothing here waits on a read**.
 *
 * The query bar, the state cell, the chips and the examples are in the markup
 * at first paint, which is what the two font preloads are for and what
 * `demo/index.html` got for free by authoring them. The two reads the page
 * needs are started here and consumed under `<Suspense>` by the parts that
 * need them: the boot call by the mode switch and the corpus list, the search
 * by the results. Awaiting either above the box left a visitor with an empty
 * 12rem rectangle for a round trip, with nothing to type into.
 *
 * Exported for its test: the page's own logic is which mode, which state word
 * and which screen, and an async Server Component inside a `<Suspense>` is not
 * something a test renderer can await.
 */
export function Console({ params }: { params: Params }) {
  const q = String(params.q ?? "").trim();
  const type = ContentType.catch("all").parse(params.type ?? "all");
  const offset = Math.max(0, Number.parseInt(String(params.offset ?? "0"), 10) || 0);
  const boot = readMeta();
  const askEnabled = boot.then((read) => read.kind === "ok" && read.meta.ask_enabled);

  // A loaded question never fires itself: an answer costs a slice of the daily
  // model budget, and neither a shared link nor a crawler may spend it.
  if (wantsAsk(params.ask === undefined ? undefined : String(params.ask), q)) {
    return (
      <AskMode initialQ={q} examples={ASK_EXAMPLES} askEnabled={askEnabled}>
        <Suspense fallback={null}>
          <CorpusPanel />
        </Suspense>
      </AskMode>
    );
  }

  const outcome = q ? searchCorpus({ q, type, offset }) : null;
  return (
    <Query
      state={outcome ? outcome.then(stateOf) : boot.then((read) => bootState(read.kind))}
      // Until the read lands: a query in the URL means the machine is already
      // scanning, and a cold page means it is ready to be typed into.
      whileWaiting={q ? "scanning" : "ready"}
      askEnabled={askEnabled}
      shape={outcome ? outcome.then(shapeOf) : null}
    >
      {outcome ? (
        <Suspense fallback={<Skeleton shape={DEFAULT_SHAPE} />}>
          <Outcome q={q} type={type} outcome={outcome} />
        </Suspense>
      ) : (
        <Cold boot={boot} />
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

/** What the *next* search reserves: the shape this one actually had. */
function shapeOf(outcome: SearchOutcome): number[] {
  if (outcome.kind !== "ok") return [];
  return groupByVideo(outcome.page.results).map((group) => group.hits.length);
}

async function Outcome({
  q,
  type,
  outcome: read,
}: {
  q: string;
  type: Channel;
  outcome: Promise<SearchOutcome>;
}) {
  const outcome = await read;
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
          {/* Every failure offers the way out of it. A bad parameter, a 5xx
              and a dropped connection are all one act away from working, and
              a page that names the failure without offering it is a dead end
              (`app.js` passed a retry to every `renderError` it made). */}
          <TryAgain />
        </div>
      </div>
    );
  }

  const { results, pagination, notes, data_status, dropped } = outcome.page;
  if (results.length === 0)
    return <NoResults q={q} type={type} dataStatus={data_status} notes={notes} dropped={dropped} />;
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
  notes,
  dropped,
}: {
  q: string;
  type: Channel;
  dataStatus: string | null;
  notes: string[];
  dropped: number;
}) {
  // A page whose every hit came back in a shape this page cannot read *did*
  // match something, so the note that says so is the whole story and the
  // headline under it would be a false one.
  const empty = dataStatus === "empty" ? <NothingIndexed /> : <NothingMatched q={q} type={type} />;
  return (
    <>
      {/* **A page with no hits still says what it could not do.** A leg that
          could not run is exactly the thing "all means all" is a promise
          about, and the page that drops the note is the page that narrowed
          the search in silence (`app.js`: `setStatus("", payload.notes)`). */}
      {notes.length ? (
        <p className={styles.status} role="status">
          {notes.map((note) => (
            <span key={note} className={styles.note}>
              {note}
            </span>
          ))}
        </p>
      ) : null}
      {dropped ? null : empty}
    </>
  );
}

// "Nothing matched" is a lie when there is nothing to match: an instance that
// has not indexed anything yet says so instead of blaming the query.
function NothingIndexed() {
  return (
    <div className={styles.notice}>
      <p className={styles.noticeTitle}>Nothing is indexed yet.</p>
    </div>
  );
}

function NothingMatched({ q, type }: { q: string; type: Channel }) {
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

// Before the first search: teach the corpus rather than show a blank page. The
// examples are copy, so they are here and not behind a read — only the two
// things the boot call actually decides wait for it.
function Cold({ boot }: { boot: Promise<MetaOutcome> }) {
  return (
    <>
      <Suspense fallback={null}>
        <BootNotice boot={boot} />
      </Suspense>
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
        <Suspense fallback={null}>
          <CorpusPanel />
        </Suspense>
      </section>
    </>
  );
}

// The 2026-08-28 defect, said out loud: `/api/meta` shares the search bucket,
// so a visitor who spent it and reloaded booted into a page of `undefined`.
async function BootNotice({ boot }: { boot: Promise<MetaOutcome> }) {
  const { kind } = await boot;
  if (kind === "ok") return null;
  return (
    <p className={styles.status} role="status">
      {kind === "rate_limited"
        ? "too many requests — this page loads again in a minute"
        : "could not reach the server"}
    </p>
  );
}

function hrefFor(example: { q: string; type?: Channel }): string {
  const params = new URLSearchParams({ ask: "0", q: example.q });
  if (example.type) params.set("type", example.type);
  return `/demo?${params}`;
}

async function CorpusPanel() {
  return <Corpus videos={await readCorpus()} />;
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
