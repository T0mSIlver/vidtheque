import Link from "next/link";
import { Suspense } from "react";
import { AskMode } from "@/components/AskMode";
import { RetryIn } from "@/components/RetryIn";
import type { MachineState } from "@/components/SearchBox";
import {
  ApiError,
  ContentType,
  type ContentType as Channel,
  type EditionResponse,
  type EditionTalk,
} from "@/lib/api";
import { api } from "@/lib/api";
import { speakerLine } from "@/lib/edition";
import { clock, receiptParts } from "@/lib/format";
import { groupByVideo } from "@/lib/group";
import { readMeta, searchCorpus, visitorIp, type SearchOutcome } from "@/lib/search";
import { wantsAsk } from "../demo/page";
import { DEFAULT_SHAPE, Query, Skeleton } from "../demo/Query";
import { Results } from "../demo/Results";
import styles from "./page.module.css";

const SLUG = "aie-paris-2026";
const TAG = "series:aie-paris-2026";
const ASK_EXAMPLES = [
  "What did the main stage say about running agents in production?",
  "Where do the speakers disagree about inference infrastructure?",
  "What practical advice appeared on the slides?",
];

type EditionOutcome =
  | { kind: "ok"; page: EditionResponse }
  | { kind: "refused"; word: "refused"; message: string; next?: string }
  | { kind: "unreachable"; word: "unavailable" };

type Params = Awaited<PageProps<"/paris">["searchParams"]>;

export default async function ParisPage(props: PageProps<"/paris">) {
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
      {/* The only `await` above the box, and it touches no network: which
          screen this is, is in the URL. */}
      <Console params={await props.searchParams} />
    </main>
  );
}

async function readEdition(): Promise<EditionOutcome> {
  try {
    return {
      kind: "ok",
      page: await api().edition(
        SLUG,
        { limit: 100, video_limit: 50 },
        { clientIp: await visitorIp(), cache: "no-store" },
      ),
    };
  } catch (error) {
    if (error instanceof ApiError) {
      return { kind: "refused", word: "refused", message: error.message, next: error.next };
    }
    return { kind: "unreachable", word: "unavailable" };
  }
}

/**
 * The schedule and the box under it — and, as at `/demo`, **nothing here waits
 * on a read**.
 *
 * This page makes two. The edition is the schedule and nothing can draw one
 * without it, so the schedule is what sits behind a `<Suspense>`. The talk
 * table it also carries only *names* what the other read brings back — a hit,
 * a citation — so it travels as a promise into the parts that print those, and
 * the query bar under the timeline is in the markup at first paint.
 *
 * Exported for its test, for the reason `/demo`'s `Console` is.
 */
export function Console({ params }: { params: Params }) {
  const q = String(params.q ?? "").trim();
  const type = ContentType.catch("all").parse(params.type ?? "all");
  const offset = Math.max(0, Number.parseInt(String(params.offset ?? "0"), 10) || 0);
  const edition = readEdition();
  const talks = edition.then((outcome) => (outcome.kind === "ok" ? outcome.page.talks : []));
  const boot = readMeta();
  const askEnabled = boot.then((read) => read.kind === "ok" && read.meta.ask_enabled);
  // Every read this page makes carries the edition's tag: `all` means all of
  // *this* corpus, and a leg that walked out of it would be another
  // conference's answer under this hero (aie-paris-2026.md §4.3).
  const outcome = q ? searchCorpus({ q, type, offset, tags: TAG }) : null;

  return (
    <>
      <Suspense fallback={<Loading />}>
        <Schedule edition={edition} />
      </Suspense>
      <section className={styles.query} aria-labelledby="query-title">
        <p className={styles.kick}>
          <s />
          <span>search or ask</span>
        </p>
        <h2 id="query-title" className={styles.sectionTitle}>
          Find a moment in the main-stage corpus
        </h2>
        {/* A loaded question never fires itself, for the reason it does not at
            `/demo`: an answer costs a slice of the daily model budget, and
            neither a shared link nor a crawler may spend it. */}
        {wantsAsk(params.ask === undefined ? undefined : String(params.ask), q) ? (
          <AskMode
            initialQ={q}
            examples={ASK_EXAMPLES}
            askEnabled={askEnabled}
            path="/paris"
            tags={TAG}
            talks={talks}
          />
        ) : (
          <Query
            state={outcome ? outcome.then(searchState) : boot.then((read) => bootState(read.kind))}
            whileWaiting={q ? "scanning" : "ready"}
            askEnabled={askEnabled}
            path="/paris"
            shape={outcome ? outcome.then(shapeOf) : null}
          >
            {outcome ? (
              <Suspense fallback={<Skeleton shape={DEFAULT_SHAPE} />}>
                <Outcome q={q} type={type} outcome={outcome} talks={talks} />
              </Suspense>
            ) : (
              <p className={styles.prompt}>
                Search every spoken sentence, slide and frame in this edition.
              </p>
            )}
          </Query>
        )}
      </section>
    </>
  );
}

// The schedule, once the edition read has landed, and the facade's own
// sentence when it did not. A refusal costs the timeline and nothing else:
// the box under it still searches this edition by its tag.
async function Schedule({ edition }: { edition: Promise<EditionOutcome> }) {
  const outcome = await edition;
  return outcome.kind === "ok" ? (
    <Timeline edition={outcome.page} />
  ) : (
    <EditionFailure outcome={outcome} />
  );
}

export function EditionFailure({ outcome }: { outcome: Exclude<EditionOutcome, { kind: "ok" }> }) {
  if (outcome.kind === "unreachable") {
    return (
      <section className={`${styles.notice} ${styles.noticeBad}`} role="status">
        <p className={styles.state}>{outcome.word}</p>
        <p>Could not reach the edition facade.</p>
        <a className={styles.retry} href="/paris">
          Retry
        </a>
      </section>
    );
  }
  return (
    <section className={`${styles.notice} ${styles.noticeBad}`} role="status">
      <p className={styles.state}>{outcome.word}</p>
      <p>{outcome.message}</p>
      {outcome.next ? <p className={styles.next}>{outcome.next}</p> : null}
    </section>
  );
}

export function Timeline({ edition }: { edition: EditionResponse }) {
  const talks = new Map(edition.talks.map((talk) => [talk.session_id, talk]));
  const main = edition.sessions.filter((session) => session.stage === "main");
  const days = [...new Set(main.map((session) => session.day))];
  const other = [
    ["discovery-1", "Discovery Track 1"],
    ["discovery-2", "Discovery Track 2"],
    ["workshop", "Workshop"],
  ] as const;
  return (
    <section className={styles.timeline} aria-labelledby="timeline-title">
      <div className={styles.sectionHead}>
        <div>
          <p className={styles.kick}>
            <s />
            <span>main stage</span>
          </p>
          <h2 id="timeline-title" className={styles.sectionTitle}>
            The programme, mapped to evidence
          </h2>
        </div>
        <a
          href={edition.edition.source_url}
          className={styles.scheduleLink}
          rel="noopener noreferrer"
        >
          Official schedule ↗
        </a>
      </div>
      {days.map((day) => (
        <div className={styles.day} key={day}>
          <h3>
            {new Intl.DateTimeFormat("en-GB", {
              weekday: "long",
              day: "numeric",
              month: "long",
              timeZone: "UTC",
            }).format(new Date(`${day}T12:00:00Z`))}
          </h3>
          <ol>
            {main
              .filter((session) => session.day === day)
              .map((session) => {
                // A main-stage session always has a talk row on the page the
                // facade returned; a row missing one is a payload the character
                // cap trimmed, and a missing row is better than a thrown page.
                const talk = talks.get(session.id);
                return talk ? <TalkRow key={session.id} talk={talk} /> : null;
              })}
          </ol>
        </div>
      ))}
      <div className={styles.otherTracks}>
        {other.map(([stage, label]) => {
          const sessions = edition.sessions.filter((session) => session.stage === stage);
          if (!sessions.length) return null;
          return (
            <details key={stage} className={styles.other}>
              <summary>
                <span>{label}</span>
                <small>not streamed; may arrive later as individual uploads</small>
              </summary>
              <ul>
                {sessions.map((session) => (
                  <li key={session.id}>
                    <time>{session.start}</time>
                    <span>
                      <b>{session.title}</b>
                      <small>
                        {session.speakers
                          .map((speaker) =>
                            speaker.company ? `${speaker.name}, ${speaker.company}` : speaker.name,
                          )
                          .join(" · ")}
                      </small>
                    </span>
                  </li>
                ))}
              </ul>
            </details>
          );
        })}
      </div>
    </section>
  );
}

// The printed proof, in the row's own quiet face rather than the slab's: the
// parser is `Receipt`'s, because there is one reading of a link on this site,
// but twenty gold blocks down a schedule would spend the accent on the list
// instead of on the moment (`components/Receipt.tsx`). A link the page cannot
// read prints none rather than one it guessed at.
function printed(link: string): string {
  const parts = receiptParts(link);
  return parts ? `${parts.host}${parts.id}${parts.query}` : "";
}

// One scheduled talk, in whichever of the three states the facade put it in.
// The two clocks stay apart: `<time>` is the hour the talk was given, and the
// offset — only ever printed when there is one — belongs to the receipt, in the
// seconds of the video it points into.
export function TalkRow({ talk }: { talk: EditionTalk }) {
  const state =
    talk.alignment_state === "not_yet_indexed"
      ? "not yet indexed"
      : talk.alignment_state === "indexed_not_aligned"
        ? "indexed, not yet aligned"
        : talk.source_kind;
  return (
    <li className={styles.talk} data-state={talk.alignment_state}>
      <time>{talk.scheduled_start}</time>
      <div className={styles.talkCopy}>
        {talk.source ? (
          <a href={talk.source} target="_blank" rel="noopener noreferrer">
            <b>{talk.title}</b>
          </a>
        ) : (
          <b>{talk.title}</b>
        )}
        <p>{speakerLine(talk)}</p>
        <small>{talk.category}</small>
        {talk.source && talk.start_s !== null ? (
          <a
            className={styles.receipt}
            href={talk.source}
            target="_blank"
            rel="noopener noreferrer"
          >
            {clock(talk.start_s)} · {printed(talk.source)}
          </a>
        ) : null}
      </div>
      <span className={styles.talkState}>{state}</span>
    </li>
  );
}

function searchState(outcome: SearchOutcome): MachineState {
  if (outcome.kind === "rate_limited" || outcome.kind === "refused") return "refused";
  if (outcome.kind === "unreachable") return "no reply";
  return outcome.page.results.length ? "ready" : "no hits";
}

function bootState(kind: "ok" | "rate_limited" | "unreachable"): MachineState {
  return kind === "ok" ? "ready" : kind === "rate_limited" ? "rate limited" : "no reply";
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
  talks: table,
}: {
  q: string;
  type: Channel;
  outcome: Promise<SearchOutcome>;
  talks: Promise<EditionTalk[]>;
}) {
  const [outcome, talks] = await Promise.all([read, table]);
  if (outcome.kind === "rate_limited")
    return <RetryIn seconds={outcome.retryAfter} variant="notice" />;
  if (outcome.kind === "refused")
    return (
      <div className={`${styles.notice} ${styles.noticeBad}`}>
        <p>{outcome.message}</p>
        {outcome.next ? <p className={styles.next}>{outcome.next}</p> : null}
      </div>
    );
  if (outcome.kind === "unreachable")
    return (
      <div className={`${styles.notice} ${styles.noticeBad}`}>
        <p>Search unavailable.</p>
        <Link href={`/paris?ask=0&q=${encodeURIComponent(q)}`} className={styles.retry}>
          Retry
        </Link>
      </div>
    );
  if (!outcome.page.results.length)
    return (
      <>
        {/* A leg that could not run is exactly what "all means all" is a
            promise about, and a page that drops the note is the page that
            narrowed the search in silence. */}
        {outcome.page.notes.length ? (
          <p className={styles.prompt} role="status">
            {outcome.page.notes.join(" · ")}
          </p>
        ) : null}
        <div className={styles.notice}>
          <p>
            {outcome.page.data_status === "empty"
              ? "Nothing is indexed yet."
              : "Nothing in this edition matches this."}
          </p>
        </div>
      </>
    );
  return (
    <Results
      q={q}
      type={type}
      hits={outcome.page.results}
      pagination={outcome.page.pagination}
      notes={outcome.page.notes}
      tags={TAG}
      talks={talks}
    />
  );
}

export function Loading() {
  return (
    <section className={styles.loading} aria-busy="true" aria-label="Loading edition">
      <p className={styles.state}>loading</p>
      {Array.from({ length: 6 }, (_, index) => (
        <span key={index} />
      ))}
    </section>
  );
}
