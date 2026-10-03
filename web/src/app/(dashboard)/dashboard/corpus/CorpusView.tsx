"use client";

import { dashboard, ROOT } from "@/lib/dashboard/client";
import { useResource } from "@/lib/dashboard/resource";
import type { Corpus } from "@/lib/dashboard/schemas";
import { at, bytes, count, hours, iso } from "@/lib/format";
import { ReadFailure } from "@/components/dashboard/kit/notice";
import {
  CountLink,
  DashLink,
  Fact,
  Figure,
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

// The five `index_state` words, each a filter on the videos table.
const VIDEO_STATES = ["ready", "pending", "indexing", "failed", "stale"] as const;

export function CorpusView() {
  const corpus = useResource("corpus", read);
  const data = corpus.data;

  if (!data && corpus.error !== undefined) {
    return <ReadFailure error={corpus.error} onRetry={corpus.reload} />;
  }

  // Local minutes like every other clock here (§24.6); the attribute keeps
  // the instant.
  const counted = data ? iso(data.counted_at) : undefined;
  return (
    <>
      <PageHead title="Corpus">
        <Fact
          label="counted"
          value={
            counted && data ? (
              <time dateTime={counted}>{at(data.counted_at)}</time>
            ) : (
              <Slot ch={16} />
            )
          }
        />
      </PageHead>
      {data ? <Loaded data={data} /> : <Pending />}
    </>
  );
}

function Loaded({ data }: { data: Corpus }) {
  const { corpus } = data;

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

        {/* §2.4: the projection does not take the byte read at all. */}
        {data.storage ? (
          <Panel id="storage" title="Storage">
            <dl className={`${ui.figures} ${ui.figuresTight}`}>
              <Figure label="keyframe JPEGs">{bytes(data.storage.keyframe_bytes)}</Figure>
              <Figure label="index file">{bytes(data.storage.database_bytes)}</Figure>
            </dl>
          </Panel>
        ) : null}
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
    </>
  );
}
