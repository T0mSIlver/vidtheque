"use client";

import { dashboard, ROOT } from "@/lib/dashboard/client";
import type { Ledger } from "@/lib/dashboard/schemas";
import { at, bytes, count, hours } from "@/lib/format";
import styles from "../dashboard.module.css";
import {
  CountLink,
  DashLink,
  Fact,
  Figure,
  GapLine,
  PageHead,
  Panel,
  Readiness,
  ReadFailure,
  Reading,
  Sep,
  Unbroken,
  Unit,
} from "../parts";
import { useRead } from "../useRead";

// The ledger — `templates/ledger.html`, reading `GET /dashboard/api/ledger`.
//
// Nothing on this page is new information: it is the counts from the overview,
// the queue's two sentences, the state filter that only existed as a URL on the
// videos table, and the byte totals, in one column of figures. Every number
// carries its label, and a number that is a door into a filtered page is the
// link — a zero is not a door, so it does not wear the accent.
//
// No chart, no axis, no history (dashboard.md §1 non-goal 5). One reading,
// taken inside one request, stamped once at the top.

const read = (signal: AbortSignal) => dashboard.ledger(signal);

// The five `index_state` words, and the jobs view's four filters. `queued` and
// `running` both link to `active`, which is exactly the filter that holds them;
// `cancelled` has no filter of its own, so it is a figure and not a link. This
// page does not invent a sixth vocabulary for either (§4.5).
const VIDEO_STATES = ["ready", "pending", "indexing", "failed", "stale"] as const;
const JOB_STATES = [
  { state: "queued", filter: "active" },
  { state: "running", filter: "active" },
  { state: "done", filter: "done" },
  { state: "failed", filter: "failed" },
  { state: "cancelled", filter: null },
] as const;

export function LedgerView() {
  const state = useRead(read);

  if (state.status === "loading") return <Reading />;
  if (state.status === "failed")
    return (
      <>
        <PageHead title="The ledger" />
        <ReadFailure error={state.error} onRetry={state.reload} />
      </>
    );
  return <Loaded data={state.data} />;
}

function Loaded({ data }: { data: Ledger }) {
  const { corpus, queue, readiness } = data;
  const failedWindowHours = Math.round(queue.failed_window_s / 3600);

  return (
    <>
      <PageHead title="The ledger">
        <Unbroken>
          <Fact label="counted" value={at(data.counted_at)} />
          <Sep />
        </Unbroken>
        <Fact label="indexed" value={at(corpus.last_indexed)} />
      </PageHead>

      {/* The band, and the page's one corner tick: the five figures that are
          the corpus. The same five the overview leads with, deliberately —
          this page is where they are all together, not a second set of them. */}
      <section aria-labelledby="corpus">
        <h2 className={styles.srOnly} id="corpus">
          The corpus
        </h2>
        <dl className={styles.ledger}>
          {/* The Jinja band carries a `published <oldest> – <newest>` note
              under this figure. `GET /dashboard/api/ledger` does not send the
              span — the overview's payload is the only one that does
              (frontend-migration.md §5 against §4) — so the note is absent
              rather than rendered as two dashes, and the gap is a line in the
              report rather than a second request for two dates. */}
          <Figure label="videos">
            <DashLink href={`${ROOT}/videos?index_state=all`}>{count(corpus.videos)}</DashLink>
          </Figure>
          <Figure label="runtime" notes={[<>{count(corpus.duration_s)} seconds indexed</>]}>
            {hours(corpus.duration_s)}
            <Unit>h</Unit>
          </Figure>
          <Figure label="transcript cues" notes={[<>in {count(corpus.chunks)} embedding chunks</>]}>
            {count(corpus.cues)}
          </Figure>
          <Figure label="keyframes" notes={[<>after near-duplicate removal</>]}>
            {count(corpus.keyframes)}
          </Figure>
          <Figure label="on-screen lines" notes={[<>read off those keyframes</>]}>
            {count(corpus.ocr_lines)}
          </Figure>
        </dl>
      </section>

      <div className={styles.split}>
        {/* The five state words, counted. They existed only as a filter on the
            videos table until now, which meant "how many are failed" was a
            question you answered by applying a filter and reading a count line.
            Each figure is the link to its own filter, so it still is — in one
            click, from a page that already told you the number. */}
        <Panel id="states" title="Videos by state">
          <dl className={`${styles.figures} ${styles.figuresTight}`}>
            {VIDEO_STATES.map((state) => (
              <Figure label={state} key={state}>
                <CountLink
                  href={`${ROOT}/videos?index_state=${state}`}
                  n={data.videos_by_state[state]}
                />
              </Figure>
            ))}
          </dl>
        </Panel>

        <Panel id="queue" title="Jobs by state">
          <dl className={`${styles.figures} ${styles.figuresTight}`}>
            {JOB_STATES.map(({ state, filter }) => (
              <Figure label={state} key={state}>
                {filter ? (
                  <CountLink href={`${ROOT}/jobs?state=${filter}`} n={data.jobs_by_state[state]} />
                ) : (
                  count(data.jobs_by_state[state])
                )}
              </Figure>
            ))}
          </dl>
          <ul className={styles.gaplist}>
            <GapLine href={`${ROOT}/jobs?state=active`} n={queue.deferred}>
              of the queued jobs are waiting on a backoff
            </GapLine>
            <GapLine href={`${ROOT}/jobs?state=failed`} n={queue.failed_recent}>
              job(s) failed in the last {failedWindowHours} hours
            </GapLine>
          </ul>
        </Panel>
      </div>

      <div className={styles.split}>
        {/* What the corpus is short of. Two of these are a re-embed in progress
            and one is a coverage gap; all three are counts of videos, and all
            three are zero on a corpus that finished everything it started. */}
        <Panel id="behind" title="What is missing">
          <dl className={`${styles.figures} ${styles.figuresTight}`}>
            <Figure label="no on-screen text" notes={[<>have a transcript, no OCR</>]}>
              <CountLink
                href={`${ROOT}/videos?has=transcript&index_state=all`}
                n={data.gaps.transcript_no_ocr}
              />
            </Figure>
            <Figure label="transcript vectors" notes={[<>chunked, waiting to embed</>]}>
              {count(data.embed_backlog.text)}
            </Figure>
            <Figure label="frame vectors" notes={[<>keyframed, waiting to embed</>]}>
              {count(data.embed_backlog.frame)}
            </Figure>
          </dl>
        </Panel>

        <Panel id="holds" title="What it is filed under">
          <dl className={`${styles.figures} ${styles.figuresTight}`}>
            <Figure label="channels">{count(corpus.channels)}</Figure>
            <Figure label="tags">{count(corpus.tags)}</Figure>
          </dl>
          {/* §2.4 keeps the corpus and drops the box: two byte totals are a
              measurement of the operator's disk, and the projection does not
              take the read at all rather than taking it and not printing it. */}
          {data.storage ? (
            <dl className={`${styles.figures} ${styles.figuresTight}`}>
              <Figure label="keyframe JPEGs">{bytes(data.storage.keyframe_bytes)}</Figure>
              <Figure label="index file">{bytes(data.storage.database_bytes)}</Figure>
            </dl>
          ) : null}
        </Panel>
      </div>

      {/* The same observation the overview makes, and the same one: a
          current-state reading with no history behind it, taken concurrently
          with the counts above so a silent worker costs this page a second at
          most (§15). The overview's model diff is not here; this page's panel
          is the strip. */}
      <Readiness readiness={readiness} redacted={data.redacted} />
    </>
  );
}
