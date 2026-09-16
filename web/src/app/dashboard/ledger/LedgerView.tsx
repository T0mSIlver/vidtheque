"use client";

import { dashboard, ROOT } from "@/lib/dashboard/client";
import { useResource } from "@/lib/dashboard/resource";
import type { Ledger } from "@/lib/dashboard/schemas";
import { at, bytes, count, DASH, hours, iso } from "@/lib/format";
import { ReadFailure } from "../kit/notice";
import { Readiness } from "../kit/Readiness";
import {
  CountLink,
  DashLink,
  Fact,
  Figure,
  GapLine,
  PageHead,
  Panel,
  Pending,
  publishedNote,
  Sep,
  ui,
  Unbroken,
  Unit,
} from "../kit/ui";

// Every key number this instance counts, in one reading stamped once
// (dashboard.md §17). No chart, no history (§1 non-goal 5).

const read = (signal: AbortSignal) => dashboard.ledger(signal);

// The five `index_state` words, and the jobs view's filters: `queued` and
// `running` both link to `active`; `cancelled` has no filter (§4.5).
const VIDEO_STATES = ["ready", "pending", "indexing", "failed", "stale"] as const;
const JOB_STATES = [
  { state: "queued", filter: "active" },
  { state: "running", filter: "active" },
  { state: "done", filter: "done" },
  { state: "failed", filter: "failed" },
  { state: "cancelled", filter: null },
] as const;

export function LedgerView() {
  const ledger = useResource("ledger", read);
  const data = ledger.data;

  if (!data && ledger.error !== undefined) {
    return <ReadFailure error={ledger.error} onRetry={ledger.reload} />;
  }

  // The instant of the reading, so it keeps its seconds.
  const counted = data ? iso(data.counted_at) : undefined;
  return (
    <>
      <PageHead title="The ledger">
        <Unbroken>
          <Fact label="counted" value={<time dateTime={counted}>{counted ?? DASH}</time>} />
          <Sep />
        </Unbroken>
        <Fact label="indexed" value={data ? at(data.corpus.last_indexed) : DASH} />
      </PageHead>
      {data ? <Loaded data={data} /> : <Pending />}
    </>
  );
}

function Loaded({ data }: { data: Ledger }) {
  const { corpus, queue, readiness } = data;
  const failedWindowHours = Math.round(queue.failed_window_s / 3600);

  return (
    <>
      <section aria-labelledby="corpus">
        <h2 className={ui.srOnly} id="corpus">
          The corpus
        </h2>
        <dl className={ui.ledger}>
          <Figure label="videos" notes={publishedNote(corpus.published)}>
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

      <div className={ui.split}>
        <Panel id="states" title="Videos by state">
          <dl className={`${ui.figures} ${ui.figuresTight}`}>
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
          <dl className={`${ui.figures} ${ui.figuresTight}`}>
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
          <ul className={ui.gaplist}>
            <GapLine href={`${ROOT}/jobs?state=active`} n={queue.deferred}>
              of the queued jobs are waiting on a backoff
            </GapLine>
            <GapLine href={`${ROOT}/jobs?state=failed`} n={queue.failed_recent}>
              job(s) failed in the last {failedWindowHours} hours
            </GapLine>
          </ul>
        </Panel>
      </div>

      <div className={ui.split}>
        <Panel id="behind" title="What is missing">
          <dl className={`${ui.figures} ${ui.figuresTight}`}>
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
          <dl className={`${ui.figures} ${ui.figuresTight}`}>
            <Figure label="channels">{count(corpus.channels)}</Figure>
            <Figure label="tags">{count(corpus.tags)}</Figure>
          </dl>
          {/* §2.4: the projection does not take the byte read at all. */}
          {data.storage ? (
            <dl className={`${ui.figures} ${ui.figuresTight}`}>
              <Figure label="keyframe JPEGs">{bytes(data.storage.keyframe_bytes)}</Figure>
              <Figure label="index file">{bytes(data.storage.database_bytes)}</Figure>
            </dl>
          ) : null}
        </Panel>
      </div>

      <Readiness
        readiness={readiness}
        redacted={data.redacted}
        writesAllowed={data.writes_allowed}
      />
    </>
  );
}
