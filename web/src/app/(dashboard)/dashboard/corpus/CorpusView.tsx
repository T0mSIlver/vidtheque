"use client";

import { dashboard, ROOT } from "@/lib/dashboard/client";
import { useResource } from "@/lib/dashboard/resource";
import type { Corpus } from "@/lib/dashboard/schemas";
import { bytes, count, hours, iso } from "@/lib/format";
import { ReadFailure } from "@/components/dashboard/kit/notice";
import {
  CountLink,
  DashLink,
  Fact,
  Figure,
  GapLine,
  PageHead,
  Panel,
  Pending,
  Slot,
  publishedNote,
  ui,
  Unit,
} from "@/components/dashboard/kit/ui";

// What is in it (dashboard.md §24.1): every count of the corpus, in one reading
// stamped once. No chart, no history (§1 non-goal 5).

const read = (signal: AbortSignal) => dashboard.corpus(signal);

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

export function CorpusView() {
  const corpus = useResource("corpus", read);
  const data = corpus.data;

  if (!data && corpus.error !== undefined) {
    return <ReadFailure error={corpus.error} onRetry={corpus.reload} />;
  }

  // The instant of the reading, so it keeps its seconds.
  const counted = data ? iso(data.counted_at) : undefined;
  return (
    <>
      <PageHead title="Corpus">
        <Fact
          label="counted"
          value={counted ? <time dateTime={counted}>{counted}</time> : <Slot ch={20} />}
        />
      </PageHead>
      {data ? <Loaded data={data} /> : <Pending />}
    </>
  );
}

function Loaded({ data }: { data: Corpus }) {
  const { corpus, queue } = data;
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
        <Panel id="channels" title="Channels">
          {data.channels.rows.length ? (
            <ul className={`${ui.rowlist} ${ui.tight}`}>
              {data.channels.rows.map((entry) => (
                <li className={ui.minirow} key={entry.channel}>
                  <DashLink
                    href={`${ROOT}/videos?channel=${encodeURIComponent(entry.channel)}&index_state=all`}
                  >
                    {entry.channel}
                  </DashLink>
                  <span className={ui.minirowFigure}>
                    {count(entry.videos)}
                    <Unit> vid</Unit>
                  </span>
                  <span className={`${ui.minirowFigure} ${ui.dim}`}>
                    {hours(entry.seconds)}
                    <Unit>h</Unit>
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className={ui.emptyNote}>No channels yet.</p>
          )}
          {data.channels.has_more ? (
            <p className={ui.emptyNote}>
              The largest {data.channels.rows.length};{" "}
              <DashLink href={`${ROOT}/videos?index_state=all`}>the videos table</DashLink> filters
              by any channel.
            </p>
          ) : null}
        </Panel>

        <Panel id="tags" title="Tags">
          {data.tags.rows.length ? (
            <ul className={ui.chiplist}>
              {data.tags.rows.map((entry) => (
                <li key={entry.tag}>
                  <DashLink
                    className={ui.chip}
                    href={`${ROOT}/videos?tags=${encodeURIComponent(entry.tag)}&index_state=all`}
                  >
                    {entry.tag} <span className={ui.chipN}>{entry.videos}</span>
                  </DashLink>
                </li>
              ))}
            </ul>
          ) : (
            <p className={ui.emptyNote}>no tags</p>
          )}
          {data.tags.has_more ? (
            <p className={ui.emptyNote}>The {data.tags.rows.length} most used.</p>
          ) : null}
        </Panel>
      </div>

      {/* §2.4: the projection does not take the byte read at all. */}
      {data.storage ? (
        <Panel id="storage" title="Storage">
          <dl className={`${ui.figures} ${ui.figuresTight}`}>
            <Figure label="keyframe JPEGs">{bytes(data.storage.keyframe_bytes)}</Figure>
            <Figure label="index file">{bytes(data.storage.database_bytes)}</Figure>
          </dl>
        </Panel>
      ) : null}
    </>
  );
}
