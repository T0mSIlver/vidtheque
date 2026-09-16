"use client";

import { dashboard, ROOT } from "@/lib/dashboard/client";
import { useResource } from "@/lib/dashboard/resource";
import type { Overview } from "@/lib/dashboard/schemas";
import { at, bytes, count, DASH, hms, hours, iso } from "@/lib/format";
import { Notice, ReadFailure } from "./kit/notice";
import { Readiness } from "./kit/Readiness";
import { table } from "./kit/table";
import {
  DashLink,
  Fact,
  Figure,
  GapLine,
  PageHead,
  Panel,
  Pending,
  publishedNote,
  Sep,
  StatePair,
  ui,
  Unbroken,
  Unit,
} from "./kit/ui";

// The corpus overview (dashboard.md §5.1): what is in the corpus, what is
// missing, what arrived last, and whether the declared models are the served
// ones. Every figure is a typed value formatted here, `hours` included.

const read = (signal: AbortSignal) => dashboard.overview(signal);

export function OverviewView() {
  const overview = useResource("overview", read);
  const data = overview.data;

  // A refusal replaces the page: a head over it would claim a reading.
  if (!data && overview.error !== undefined) {
    return <ReadFailure error={overview.error} onRetry={overview.reload} />;
  }

  return (
    <>
      <PageHead title="Corpus overview">
        <Unbroken>
          {data ? (
            <StatePair label="data_status" word={data.corpus.data_status} />
          ) : (
            <Fact label="data_status" value={DASH} />
          )}
          <Sep />
        </Unbroken>
        <Fact label="indexed" value={data ? at(data.corpus.last_indexed) : DASH} />
      </PageHead>
      {data ? <Loaded data={data} /> : <Pending />}
    </>
  );
}

function Loaded({ data }: { data: Overview }) {
  const { corpus, gaps, jobs, readiness } = data;
  const projection = data.redacted;
  const failedWindowHours = Math.round(jobs.failed_window_s / 3600);

  // The flag is this payload's own (§19), so the banner paints with the page.
  // The projection keeps the effect on search and drops the operator's reason.
  const writesRefused = !data.writes_allowed && !projection;
  const drift = !readiness.vectors.enabled || writesRefused;

  return (
    <>
      {drift ? (
        projection ? (
          <Notice
            id="drift"
            tone="bad"
            title="Vector search is off on this instance"
            next="Search still answers from full-text, and every response says so."
          />
        ) : (
          <Notice
            id="drift"
            tone="bad"
            title="The corpus and the worker disagree"
            detail={readiness.vectors.reason}
            next={
              <>
                Search still answers from full-text; the vector legs are off and every response says
                so. Indexing is{" "}
                {writesRefused ? "refused, so no video can mix embedding spaces" : "still allowed"}.
              </>
            }
          />
        )
      ) : null}

      <section aria-labelledby="figures">
        <h2 className={ui.srOnly} id="figures">
          What is in it
        </h2>
        <dl className={ui.ledger}>
          <Figure
            label="videos"
            notes={[
              // `videos_ready`, not `queryable_videos` (ready plus stale).
              <>
                {count(corpus.videos_ready)} ready
                {corpus.videos - corpus.videos_ready > 0 ? (
                  <>
                    {" · "}
                    <DashLink href={`${ROOT}/videos?index_state=all&order=indexed_at`}>
                      {count(corpus.videos - corpus.videos_ready)} not ready
                    </DashLink>
                  </>
                ) : null}
              </>,
              ...publishedNote(corpus.published),
            ]}
          >
            {count(corpus.videos)}
          </Figure>
          <Figure label="runtime" notes={[<>{count(corpus.duration_s)} seconds indexed</>]}>
            {hours(corpus.duration_s)}
            <Unit>h</Unit>
          </Figure>
          <Figure label="transcript cues" notes={[<>timed lines of speech</>]}>
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

      {/* Declared beside served: the two tables only mean something together. */}
      <Readiness
        drift={drift}
        readiness={readiness}
        redacted={projection}
        writesAllowed={data.writes_allowed}
      >
        {data.declared_models?.length || readiness.worker?.models.length ? (
          <div className={`${ui.split} ${table.models}`}>
            {data.declared_models?.length ? (
              <div className={table.tablewrap}>
                <table className={table.grid}>
                  <caption className={ui.srOnly}>The models this corpus was built with</caption>
                  <thead>
                    <tr>
                      <th scope="col">stage</th>
                      <th scope="col">model declared</th>
                      <th scope="col" className={table.num}>
                        dim
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.declared_models.map((model) => (
                      <tr key={model.key}>
                        <th scope="row">{model.label}</th>
                        <td>
                          <code>{model.value}</code>
                        </td>
                        <td className={table.num}>{model.dim || DASH}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
            {readiness.worker?.models.length ? (
              <div className={table.tablewrap}>
                <table className={table.grid}>
                  <caption className={ui.srOnly}>The models the worker reports serving</caption>
                  <thead>
                    <tr>
                      <th scope="col">worker task</th>
                      <th scope="col">model served</th>
                      <th scope="col">memory</th>
                    </tr>
                  </thead>
                  <tbody>
                    {readiness.worker.models.map((model) => (
                      <tr key={model.task}>
                        <th scope="row">
                          <code>{model.task}</code>
                        </th>
                        <td>
                          <code>{model.model}</code>
                        </td>
                        <td>{model.loaded ? "loaded" : "cold"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
          </div>
        ) : null}
      </Readiness>

      <div className={`${ui.split} ${ui.splitMain}`}>
        <Panel id="recent" title="Recently indexed">
          {data.recent.length ? (
            <ul className={ui.rowlist}>
              {data.recent.map((video) => (
                <li className={ui.row} key={video.video_id}>
                  <DashLink
                    className={ui.shot}
                    href={`${ROOT}/videos/${encodeURIComponent(video.video_id)}`}
                    tabIndex={-1}
                    aria-hidden="true"
                  >
                    {video.thumb ? (
                      // A signed, expiring frame URL already at display width.
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        className={ui.thumb}
                        src={video.thumb}
                        alt=""
                        width={96}
                        height={54}
                        loading="lazy"
                        decoding="async"
                      />
                    ) : (
                      <span className={`${ui.thumb} ${ui.thumbEmpty}`}>no frame</span>
                    )}
                  </DashLink>
                  <div className={ui.rowBody}>
                    <DashLink
                      className={ui.rowTitle}
                      href={`${ROOT}/videos/${encodeURIComponent(video.video_id)}`}
                    >
                      {video.title}
                    </DashLink>
                    <p className={ui.rowMeta}>
                      {video.channel}
                      <Sep />
                      <span className={ui.mono}>{hms(video.duration_s)}</span>
                    </p>
                  </div>
                  <p className={ui.rowWhen}>
                    indexed{" "}
                    <time className={ui.mono} dateTime={iso(video.indexed_at)}>
                      {at(video.indexed_at)}
                    </time>
                  </p>
                </li>
              ))}
            </ul>
          ) : (
            <p className={ui.emptyNote}>
              Nothing has finished indexing yet. Add one with <code>index-video</code>.
            </p>
          )}
        </Panel>

        <div>
          {/* First: the one panel about something happening now. */}
          <Panel id="queue" title="The queue">
            <ul className={ui.gaplist}>
              <GapLine href={`${ROOT}/jobs?state=active`} n={jobs.active}>
                job(s) queued or running
                {jobs.deferred ? (
                  <>
                    ,{" "}
                    <span className={ui.warn}>
                      {count(jobs.deferred)} of them waiting on a backoff
                    </span>
                  </>
                ) : null}
              </GapLine>
              <GapLine href={`${ROOT}/jobs?state=failed`} n={jobs.failed_recent}>
                job(s) failed in the last {failedWindowHours} hours
              </GapLine>
            </ul>
          </Panel>

          {/* Three zeros is not a panel; the queue above stays even when empty. */}
          {gaps.transcript_no_ocr + gaps.indexing + gaps.failed > 0 ? (
            <Panel id="gaps" title="What is missing">
              <ul className={ui.gaplist}>
                <GapLine
                  href={`${ROOT}/videos?has=transcript&index_state=all`}
                  n={gaps.transcript_no_ocr}
                >
                  video(s) have a transcript but no on-screen text
                </GapLine>
                <GapLine href={`${ROOT}/videos?index_state=indexing`} n={gaps.indexing}>
                  video(s) are mid-pipeline
                </GapLine>
                {/* A capped probe prints `N+`, off the payload's own flag. */}
                <GapLine
                  href={`${ROOT}/videos?index_state=failed`}
                  n={gaps.failed}
                  figure={gaps.failed_capped ? `${count(gaps.failed)}+` : undefined}
                >
                  video(s) are marked failed with a failed stage behind it
                </GapLine>
              </ul>
            </Panel>
          ) : null}

          {/* §2.4: the projection does not take the byte read at all. */}
          {data.storage ? (
            <Panel id="storage" title="Storage">
              <dl className={ui.figures}>
                <Figure label="keyframe JPEGs">{bytes(data.storage.keyframe_bytes)}</Figure>
                <Figure label="index">{bytes(data.storage.database_bytes)}</Figure>
              </dl>
            </Panel>
          ) : null}
        </div>
      </div>

      <div className={ui.split}>
        <Panel id="channels" title="Channels">
          {data.channels.length ? (
            <ul className={`${ui.rowlist} ${ui.tight}`}>
              {data.channels.map((entry) => (
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
        </Panel>

        <Panel id="tags" title="Tags">
          {data.tags.length ? (
            <ul className={ui.chiplist}>
              {data.tags.map((entry) => (
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
        </Panel>
      </div>
    </>
  );
}
