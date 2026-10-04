"use client";

import type { ReactNode } from "react";
import { dashboard, ROOT } from "@/lib/dashboard/client";
import { useResource } from "@/lib/dashboard/resource";
import type { Health } from "@/lib/dashboard/schemas";
import { at, count } from "@/lib/format";
import { Notice, ReadFailure } from "@/components/dashboard/kit/notice";
import {
  AllClear,
  CountLink,
  DashLink,
  Page,
  PageHead,
  Panel,
  Pending,
  ui,
} from "@/components/dashboard/kit/ui";
import { CostsPanel } from "./Costs";
import styles from "./health.module.css";

// Is the machine working right now (dashboard.md §24.1, §28.2): problems at
// the top, then the pipeline, the queue and what the model calls cost. What
// is in the corpus is Corpus's.

const read = (signal: AbortSignal) => dashboard.health(signal);

// `data_status` (corpus-summary §4.3) as the head's sentence.
const STATUS: Record<string, string> = {
  ok: "Index up to date",
  indexing: "Indexing now",
  deferred: "Waiting on a retry",
  degraded: "Some data is missing",
  partial: "Some videos have no on-screen text",
  empty: "No videos yet",
};

// The worker's slots (worker/backends/base.py `TASKS`) in the declared list's
// words.
const TASKS: Record<string, string> = {
  stt: "transcription",
  embed: "text embeddings",
  image_embed: "frame embeddings",
  ocr: "on-screen text",
};

const plural = (n: number, one: string, many: string) => `${count(n)} ${n === 1 ? one : many}`;

export function HealthView() {
  const health = useResource("health", read);
  const data = health.data;

  // A refusal replaces the page: a head over it would claim a reading.
  if (!data && health.error !== undefined) {
    return <ReadFailure error={health.error} onRetry={health.reload} />;
  }

  return (
    <Page>
      <PageHead title="Health" note={data ? headNote(data) : <>&nbsp;</>} />
      {data ? <Loaded data={data} /> : <Pending />}
    </Page>
  );
}

function headNote(data: Health): ReactNode {
  const status = STATUS[data.data_status] ?? data.data_status;
  return data.last_indexed ? (
    <>
      {status}, last video indexed <time className={styles.when}>{at(data.last_indexed)}</time>
    </>
  ) : (
    status
  );
}

function Loaded({ data }: { data: Health }) {
  return (
    <>
      <Alerts data={data} />
      <Pipeline data={data} />
      <Queue data={data} />
      <CostsPanel />
    </>
  );
}

/** Everything that needs acting on, before anything else. */
function Alerts({ data }: { data: Health }) {
  const { readiness, jobs, gaps } = data;
  const projection = data.redacted;
  // The flag is this payload's own (§19), so the alert paints with the page.
  const writesRefused = !data.writes_allowed && !projection;
  const drift = !readiness.vectors.enabled || writesRefused;
  const worker = readiness.worker;
  const failedHours = Math.round(jobs.failed_window_s / 3600);

  return (
    <>
      {drift ? (
        projection ? (
          <Notice
            id="drift"
            tone="bad"
            title="Vector search is off on this instance"
            next="Search answers from full-text only."
          />
        ) : (
          <Notice
            id="drift"
            tone="bad"
            title="The corpus and the worker disagree"
            detail={readiness.vectors.reason}
            next={
              writesRefused
                ? "Search answers from full-text only, and indexing is refused so no video mixes embedding spaces."
                : "Search answers from full-text only."
            }
          />
        )
      ) : null}
      {worker?.state === "unavailable" ? (
        <Notice
          id="worker"
          tone="bad"
          title="The worker is not answering"
          detail={worker.detail}
          next="Nothing new is indexed until it answers."
        />
      ) : null}
      {jobs.failed_recent ? (
        <Notice
          id="failed-jobs"
          tone="warn"
          title={`${plural(jobs.failed_recent, "job", "jobs")} failed in the last ${failedHours} hours`}
          next={<DashLink href={`${ROOT}/jobs?state=failed`}>Show failed jobs</DashLink>}
        />
      ) : null}
      {gaps.has_failed ? (
        <Notice
          id="failed-videos"
          tone="warn"
          title="Some videos failed to index"
          next={<DashLink href={`${ROOT}/videos?index_state=failed`}>Show failed videos</DashLink>}
        />
      ) : null}
    </>
  );
}

/** The services as words, then the models the corpus was built with and the
 *  ones the worker serves. The projection carries neither the worker nor the
 *  indexing state (§24.1). */
function Pipeline({ data }: { data: Health }) {
  const { readiness } = data;
  const worker = readiness.worker;
  const services: [string, string, Tone][] = [
    ["MCP", readiness.mcp, "ok"],
    ["Database", readiness.database, "ok"],
  ];
  if (worker) {
    services.push([
      "Worker",
      worker.state,
      worker.state === "ready" ? "ok" : worker.state === "unavailable" ? "bad" : "wait",
    ]);
  }
  services.push([
    "Vector search",
    readiness.vectors.enabled ? "ready" : "full-text only",
    readiness.vectors.enabled ? "ok" : "bad",
  ]);
  if (!data.redacted) {
    services.push([
      "Indexing",
      data.writes_allowed ? "allowed" : "refused",
      data.writes_allowed ? "ok" : "bad",
    ]);
  }
  const declared = data.declared_models ?? [];
  const served = worker?.models ?? [];

  return (
    <Panel id="pipeline" title="Pipeline">
      <ul className={styles.services}>
        {services.map(([name, word, tone]) => (
          <li key={name}>
            {name} <span data-tone={tone}>{word}</span>
          </li>
        ))}
      </ul>
      {declared.length || served.length ? (
        <div className={styles.models}>
          {declared.length ? (
            <div>
              <p className={styles.listHead}>Built with</p>
              <dl className={styles.modelList}>
                {declared.map((model) => (
                  <div key={model.key}>
                    <dt>{model.label}</dt>
                    <dd>
                      <code>{model.value}</code>
                    </dd>
                  </div>
                ))}
              </dl>
            </div>
          ) : null}
          {served.length ? (
            <div>
              <p className={styles.listHead}>Worker serves</p>
              <dl className={styles.modelList}>
                {served.map((model) => (
                  <div key={model.task}>
                    <dt>{TASKS[model.task] ?? model.task.replaceAll("_", " ")}</dt>
                    <dd>
                      <code>{model.model}</code>{" "}
                      <span className={ui.muted}>{model.loaded ? "loaded" : "cold"}</span>
                    </dd>
                  </div>
                ))}
              </dl>
            </div>
          ) : null}
        </div>
      ) : null}
    </Panel>
  );
}

type Tone = "ok" | "bad" | "wait";

/** What is queued and what is missing, zeros left out. Failures are alerts
 *  above, so they are not counted again here. */
function Queue({ data }: { data: Health }) {
  const { gaps, embed_backlog: backlog, jobs } = data;
  const lines: ReactNode[] = [];
  if (jobs.active) {
    lines.push(
      <li key="active">
        <CountLink href={`${ROOT}/jobs?state=active`} n={jobs.active} />
        <span>
          {jobs.active === 1 ? "job queued or running" : "jobs queued or running"}
          {jobs.deferred ? (
            <span className={ui.warn}>, {count(jobs.deferred)} waiting on a retry</span>
          ) : null}
        </span>
      </li>,
    );
  }
  if (gaps.indexing) {
    lines.push(
      <li key="indexing">
        <CountLink href={`${ROOT}/videos?index_state=indexing`} n={gaps.indexing} />
        <span>{gaps.indexing === 1 ? "video mid-pipeline" : "videos mid-pipeline"}</span>
      </li>,
    );
  }
  if (backlog.text) {
    lines.push(
      <li key="text">
        <span className={ui.figureCount}>{count(backlog.text)}</span>
        <span>{backlog.text === 1 ? "video waits" : "videos wait"} to embed the transcript</span>
      </li>,
    );
  }
  if (backlog.frame) {
    lines.push(
      <li key="frame">
        <span className={ui.figureCount}>{count(backlog.frame)}</span>
        <span>{backlog.frame === 1 ? "video waits" : "videos wait"} to embed the frames</span>
      </li>,
    );
  }
  if (gaps.transcript_no_ocr) {
    lines.push(
      <li key="ocr">
        <CountLink
          href={`${ROOT}/videos?has=transcript&index_state=all`}
          n={gaps.transcript_no_ocr}
        />
        <span>
          {gaps.transcript_no_ocr === 1 ? "video has" : "videos have"} a transcript but no on-screen
          text
        </span>
      </li>,
    );
  }
  return (
    <Panel id="queue" title="Queue">
      {lines.length ? (
        <ul className={ui.gaplist}>{lines}</ul>
      ) : (
        <AllClear>Queue empty, nothing missing.</AllClear>
      )}
    </Panel>
  );
}
