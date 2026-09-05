"use client";

import { dashboard, ROOT } from "@/lib/dashboard/client";
import type { Overview } from "@/lib/dashboard/schemas";
import { at, bytes, count, day, duration, hours, iso } from "@/lib/format";
import styles from "./dashboard.module.css";
import {
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
  StatePair,
  Unbroken,
  Unit,
} from "./parts";
import { useSession } from "./session";
import { useRead } from "./useRead";

// The corpus overview — `templates/overview.html`, reading
// `GET /dashboard/api/overview` in the browser instead of being rendered from
// the same query in Python.
//
// It is a ledger, not a dashboard of cards: what is in the corpus, what is
// missing from it, what arrived last, and whether the models the corpus was
// built with are still the ones being served. Read top to bottom it is the
// answer to "what happened while I was asleep".
//
// Every figure here is a value Python sent typed and this file formats
// (frontend-migration.md §1 decision 5) — including `hours`, which is
// deliberately *not* on the wire, because the rollup's own comment calls it a
// display rounding and deriving seconds back out of it once reported a 149 s
// corpus as 0.

const read = (signal: AbortSignal) => dashboard.overview(signal);

export function OverviewView() {
  const state = useRead(read);

  if (state.status === "loading") return <Reading />;
  if (state.status === "failed")
    return (
      <>
        <PageHead title="Corpus overview" />
        <ReadFailure error={state.error} onRetry={state.reload} />
      </>
    );
  return <Loaded data={state.data} />;
}

function Loaded({ data }: { data: Overview }) {
  const session = useSession();
  const { corpus, gaps, jobs, readiness } = data;
  // The projection's own word for itself. `redacted` and the session's
  // `readonly` are the same flag read on two endpoints; this page has the
  // payload in hand, so it asks the payload.
  const projection = data.redacted;
  const failedWindowHours = Math.round(jobs.failed_window_s / 3600);

  // The drift banner is the private half of §2.4's overview row, and the
  // projection keeps the half a visitor can act on. The *reason* is written for
  // whoever set the env; the *effect* — that search is answering from full-text
  // alone — changes what a visitor should believe about the results, so it
  // survives with the operator's sentence cut out of it.
  const writesRefused = Boolean(session && !session.writes_allowed && !projection);
  const drift = !readiness.vectors.enabled || writesRefused;

  return (
    <>
      <PageHead title="Corpus overview">
        <Unbroken>
          <StatePair label="data_status" word={corpus.data_status} />
          <Sep />
        </Unbroken>
        <Fact label="indexed" value={at(corpus.last_indexed)} />
      </PageHead>

      {drift ? (
        <section className={styles.notice} aria-labelledby="drift">
          {projection ? (
            <>
              <h2 className={styles.noticeTitle} id="drift">
                Vector search is off on this instance
              </h2>
              <p className={styles.noticeNext}>
                Search still answers from full-text, and every response says so.
              </p>
            </>
          ) : (
            <>
              <h2 className={styles.noticeTitle} id="drift">
                The corpus and the worker disagree
              </h2>
              {readiness.vectors.reason ? (
                <p className={styles.noticeDetail}>{readiness.vectors.reason}</p>
              ) : null}
              <p className={styles.noticeNext}>
                Search still answers from full-text; the vector legs are off and every response says
                so. Indexing is{" "}
                {writesRefused ? "refused, so no video can mix embedding spaces" : "still allowed"}.
              </p>
            </>
          )}
        </section>
      ) : null}

      {/* The band. Five counts across the full measure, divided by hairlines,
          set in the machine face — the whole corpus as one line in a log. */}
      <section aria-labelledby="figures">
        <h2 className={styles.srOnly} id="figures">
          What is in it
        </h2>
        <dl className={styles.ledger}>
          <Figure
            label="videos"
            notes={[
              <>
                {count(corpus.queryable_videos)} ready
                {corpus.videos - corpus.queryable_videos > 0 ? (
                  <>
                    {" · "}
                    <DashLink href={`${ROOT}/videos?index_state=all&order=indexed_at`}>
                      {count(corpus.videos - corpus.queryable_videos)} not ready
                    </DashLink>
                  </>
                ) : null}
              </>,
              // When *these videos* were published, which is a fact about the
              // corpus's contents and not about its state. The two dates are
              // one unbreakable unit, so the note wraps in front of the range
              // and never inside it.
              <>
                published{" "}
                <Unbroken>
                  <span className={styles.mono}>{day(corpus.published.oldest)}</span>
                  <Sep>–</Sep>
                  <span className={styles.mono}>{day(corpus.published.newest)}</span>
                </Unbroken>
              </>,
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

      {/* One panel, and it is a diff: what the pipeline reports right now on
          top as states, and what it was *declared* with beside what the worker
          says it is *serving* — those two tables only mean something read
          against each other. In the projection both are absent rather than
          redacted in place. */}
      <Readiness readiness={readiness} redacted={projection} drift={drift}>
        {data.declared_models?.length || readiness.worker?.models.length ? (
          <div className={`${styles.split} ${styles.models}`}>
            {data.declared_models?.length ? (
              <div className={styles.tablewrap}>
                <table className={styles.grid}>
                  <caption className={styles.srOnly}>The models this corpus was built with</caption>
                  <thead>
                    <tr>
                      <th scope="col">stage</th>
                      <th scope="col">model declared</th>
                      <th scope="col" className={styles.num}>
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
                        <td className={styles.num}>{model.dim || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
            {readiness.worker?.models.length ? (
              <div className={styles.tablewrap}>
                <table className={styles.grid}>
                  <caption className={styles.srOnly}>The models the worker reports serving</caption>
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

      {/* The zone: what arrived (dense, wants the width) beside what is wrong
          and what it costs (short answers that never wanted 1400px). */}
      <div className={`${styles.split} ${styles.splitMain}`}>
        <Panel id="recent" title="Recently indexed">
          {data.recent.length ? (
            <ul className={styles.rowlist}>
              {data.recent.map((video) => (
                <li className={styles.row} key={video.video_id}>
                  <DashLink
                    className={styles.shot}
                    href={`${ROOT}/videos/${video.video_id}`}
                    tabIndex={-1}
                    aria-hidden="true"
                  >
                    {video.thumb ? (
                      // A signed, expiring `/frames/…` URL on Python's origin,
                      // already sized by the API at the width it is displayed
                      // at. The optimizer would fetch and cache it past its own
                      // signature, for a 96px still.
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        className={styles.thumb}
                        src={video.thumb}
                        alt=""
                        width={96}
                        height={54}
                        loading="lazy"
                        decoding="async"
                      />
                    ) : (
                      <span className={`${styles.thumb} ${styles.thumbEmpty}`}>no frame</span>
                    )}
                  </DashLink>
                  <div className={styles.rowBody}>
                    <DashLink className={styles.rowTitle} href={`${ROOT}/videos/${video.video_id}`}>
                      {video.title}
                    </DashLink>
                    {/* The break opportunity sits after the separator: a
                        conference channel name is long enough to wrap, and when
                        it does the runtime should start the next line. */}
                    <p className={styles.rowMeta}>
                      {video.channel}
                      <Sep />
                      <span className={styles.mono}>{duration(video.duration_s)}</span>
                    </p>
                  </div>
                  <p className={styles.rowWhen}>
                    indexed{" "}
                    <time className={styles.mono} dateTime={iso(video.indexed_at)}>
                      {at(video.indexed_at)}
                    </time>
                  </p>
                </li>
              ))}
            </ul>
          ) : (
            <p className={styles.emptyNote}>
              Nothing has finished indexing yet. Add one with <code>index-video</code>.
            </p>
          )}
        </Panel>

        <div>
          {/* First in this column because it is the only panel on the page
              describing something happening *now*: the operator who arrives
              mid-batch reads this line, clicks the figure next to the thing
              that is wrong, and is on the jobs view. */}
          <Panel id="queue" title="The queue">
            <ul className={styles.gaplist}>
              <GapLine href={`${ROOT}/jobs?state=active`} n={jobs.active}>
                job(s) queued or running
                {jobs.deferred ? (
                  <>
                    ,{" "}
                    <span className={styles.warn}>
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

          {/* Three zeros is not a panel. This block answers "what should I go
              and look at", and when the answer is nothing it was three rows of
              `0` taking a third of the column to say so. Nothing is hidden:
              every figure is a link into a filter one click away. The queue
              panel above deliberately does not do this — a queue that is empty
              right now is a fact about this second. */}
          {gaps.transcript_no_ocr + gaps.indexing + gaps.failed > 0 ? (
            <Panel id="gaps" title="What is missing">
              <ul className={styles.gaplist}>
                <GapLine
                  href={`${ROOT}/videos?has=transcript&index_state=all`}
                  n={gaps.transcript_no_ocr}
                >
                  video(s) have a transcript but no on-screen text
                </GapLine>
                <GapLine href={`${ROOT}/videos?index_state=indexing`} n={gaps.indexing}>
                  video(s) are mid-pipeline
                </GapLine>
                {/* The rollup probes this one with LIMIT 5, so five means "five
                    or more" and the page says so rather than reporting a cap as
                    a count. */}
                <GapLine href={`${ROOT}/videos?index_state=failed`} n={gaps.failed}>
                  video(s) are marked failed with a failed stage behind it
                </GapLine>
              </ul>
            </Panel>
          ) : null}

          {/* §2.4: the demo overview carries the corpus, not the box it is on.
              Two byte totals are a measurement of somebody's disk, and the
              projection does not take that read at all. */}
          {data.storage ? (
            <Panel id="storage" title="Storage">
              <dl className={styles.figures}>
                <Figure label="keyframe JPEGs">{bytes(data.storage.keyframe_bytes)}</Figure>
                <Figure label="index">{bytes(data.storage.database_bytes)}</Figure>
              </dl>
            </Panel>
          ) : null}
        </div>
      </div>

      <div className={styles.split}>
        <Panel id="channels" title="Channels">
          {data.channels.length ? (
            <ul className={`${styles.rowlist} ${styles.tight}`}>
              {data.channels.map((entry) => (
                <li className={styles.minirow} key={entry.channel}>
                  <DashLink
                    href={`${ROOT}/videos?channel=${encodeURIComponent(entry.channel)}&index_state=all`}
                  >
                    {entry.channel}
                  </DashLink>
                  <span className={styles.minirowFigure}>
                    {count(entry.videos)}
                    <Unit> vid</Unit>
                  </span>
                  <span className={`${styles.minirowFigure} ${styles.dim}`}>
                    {hours(entry.seconds)}
                    <Unit>h</Unit>
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className={styles.emptyNote}>No channels yet.</p>
          )}
        </Panel>

        <Panel id="tags" title="Tags">
          {data.tags.length ? (
            <ul className={styles.chiplist}>
              {data.tags.map((entry) => (
                <li key={entry.tag}>
                  <DashLink
                    className={styles.chip}
                    href={`${ROOT}/videos?tags=${encodeURIComponent(entry.tag)}&index_state=all`}
                  >
                    {entry.tag} <span className={styles.chipN}>{entry.videos}</span>
                  </DashLink>
                </li>
              ))}
            </ul>
          ) : (
            // "no tags", and nothing after it: the sentence that followed
            // taught `<namespace>:<name>`, a format lesson on the surface whose
            // whole brief is that it does not narrate.
            <p className={styles.emptyNote}>no tags</p>
          )}
        </Panel>
      </div>
    </>
  );
}
