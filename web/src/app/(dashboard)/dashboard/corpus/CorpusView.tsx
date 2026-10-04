"use client";

import type { ReactNode } from "react";
import { dashboard, ROOT } from "@/lib/dashboard/client";
import { useResource } from "@/lib/dashboard/resource";
import type { Corpus } from "@/lib/dashboard/schemas";
import { bytes, count, day, hours } from "@/lib/format";
import { Fold } from "@/components/dashboard/kit/Fold";
import { ReadFailure } from "@/components/dashboard/kit/notice";
import { DashLink, Page, PageHead, Panel, Pending, Sep, ui } from "@/components/dashboard/kit/ui";
import styles from "./corpus.module.css";
import { ValuedTimePanel } from "./ValuedTime";

// What is in it (dashboard.md §24.1, §28.3): the size in one sentence, the
// feed against YouTube, the channels and tags as doors into the videos table,
// and every other count on demand. No chart, no history (§1 non-goal 5).

const read = (signal: AbortSignal) => dashboard.corpus(signal);

// The five `index_state` words, each a filter on the videos table.
const VIDEO_STATES = ["ready", "pending", "indexing", "failed", "stale"] as const;

export function CorpusView() {
  const corpus = useResource("corpus", read);
  const data = corpus.data;

  if (!data && corpus.error !== undefined) {
    return <ReadFailure error={corpus.error} onRetry={corpus.reload} />;
  }

  return (
    <Page>
      <PageHead title="Corpus" note={data ? <Size data={data} /> : <>&nbsp;</>} />
      {data ? <Loaded data={data} /> : <Pending />}
    </Page>
  );
}

/** `669 videos, 290 hours, published 2025-12-27 – 2026-10-03`. */
function Size({ data }: { data: Corpus }) {
  const { corpus } = data;
  const { oldest, newest } = corpus.published;
  return (
    <>
      <DashLink href={`${ROOT}/videos?index_state=all`}>
        {count(corpus.videos)} {corpus.videos === 1 ? "video" : "videos"}
      </DashLink>
      , {hours(corpus.duration_s)} hours
      {oldest !== null || newest !== null ? (
        <>
          , published{" "}
          <span className={ui.nowrap}>
            {day(oldest)}
            <Sep>–</Sep>
            {day(newest)}
          </span>
        </>
      ) : null}
    </>
  );
}

function Loaded({ data }: { data: Corpus }) {
  return (
    <>
      <ValuedTimePanel />
      <Lists data={data} />
      <Counts data={data} />
    </>
  );
}

/** The channels and the tags, each one a filter on the videos table. */
function Lists({ data }: { data: Corpus }) {
  return (
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
                <span className={styles.rowFigure}>
                  {count(entry.videos)} {entry.videos === 1 ? "video" : "videos"} ·{" "}
                  {hours(entry.seconds)} h
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className={ui.emptyNote}>No channels yet.</p>
        )}
        {data.channels.has_more ? (
          <p className={ui.emptyNote}>The largest {data.channels.rows.length}.</p>
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
          <p className={ui.emptyNote}>No tags yet.</p>
        )}
        {data.tags.has_more ? (
          <p className={ui.emptyNote}>The {data.tags.rows.length} most used.</p>
        ) : null}
      </Panel>
    </div>
  );
}

/** Every other count, read on demand. A state with no video is left out.
 *  §2.4: the projection does not take the byte read. */
function Counts({ data }: { data: Corpus }) {
  const { corpus } = data;
  const states = VIDEO_STATES.filter((state) => data.videos_by_state[state] > 0);
  const rows: [string, ReactNode][] = [
    [
      "Videos by state",
      states.length ? (
        <span className={styles.states}>
          {states.map((state) => (
            <DashLink key={state} href={`${ROOT}/videos?index_state=${state}`}>
              {count(data.videos_by_state[state])} {state}
            </DashLink>
          ))}
        </span>
      ) : (
        "none"
      ),
    ],
    [
      "Transcript cues",
      <>
        {count(corpus.cues)} <span className={ui.muted}>in {count(corpus.chunks)} chunks</span>
      </>,
    ],
    ["Keyframes", count(corpus.keyframes)],
    ["On-screen text lines", count(corpus.ocr_lines)],
  ];
  if (data.storage) {
    rows.push(["Keyframe images", bytes(data.storage.keyframe_bytes)]);
    rows.push(["Index file", bytes(data.storage.database_bytes)]);
  }
  return (
    <section className={styles.more} aria-label="Counts">
      <Fold label="every count">
        <dl className={styles.counts}>
          {rows.map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      </Fold>
    </section>
  );
}
