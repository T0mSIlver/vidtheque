"use client";

import { dashboard, ROOT } from "@/lib/dashboard/client";
import { useResource } from "@/lib/dashboard/resource";
import type { Health } from "@/lib/dashboard/schemas";
import { at, count, DASH } from "@/lib/format";
import { Notice, ReadFailure } from "@/components/dashboard/kit/notice";
import { Readiness } from "@/components/dashboard/kit/Readiness";
import { table } from "@/components/dashboard/kit/table";
import {
  DashLink,
  Fact,
  GapLine,
  PageHead,
  Panel,
  Pending,
  Sep,
  StatePair,
  ui,
  Unbroken,
} from "@/components/dashboard/kit/ui";
import { CostsPanel } from "./Costs";

// Is the machine working right now (dashboard.md §24.1): readiness, the
// models, the queue, what is missing and what the model calls cost. What is
// in the corpus is Corpus's.

const read = (signal: AbortSignal) => dashboard.health(signal);

export function HealthView() {
  const health = useResource("health", read);
  const data = health.data;

  // A refusal replaces the page: a head over it would claim a reading.
  if (!data && health.error !== undefined) {
    return <ReadFailure error={health.error} onRetry={health.reload} />;
  }

  return (
    <>
      <PageHead title="Health">
        <Unbroken>
          {data ? (
            <StatePair label="data_status" word={data.data_status} />
          ) : (
            <Fact label="data_status" value={DASH} />
          )}
          <Sep />
        </Unbroken>
        <Fact label="indexed" value={data ? at(data.last_indexed) : DASH} />
      </PageHead>
      {data ? <Loaded data={data} /> : <Pending />}
    </>
  );
}

function Loaded({ data }: { data: Health }) {
  const { gaps, embed_backlog: backlog, jobs, readiness } = data;
  const projection = data.redacted;
  const failedWindowHours = Math.round(jobs.failed_window_s / 3600);
  const missing =
    gaps.transcript_no_ocr + gaps.indexing + backlog.text + backlog.frame > 0 || gaps.has_failed;

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

      <div className={ui.split}>
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

        {/* All zeros is not a panel; the queue beside it stays even when empty. */}
        {missing ? (
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
              <GapLine n={backlog.text}>video(s) waiting to embed their transcript</GapLine>
              <GapLine n={backlog.frame}>video(s) waiting to embed their frames</GapLine>
              {/* The count is Corpus's (§24): a door, not a second copy of it. */}
              {gaps.has_failed ? (
                <li>
                  <DashLink href={`${ROOT}/corpus#states`}>Corpus</DashLink>
                  <span className={ui.warn}>counts the videos marked failed</span>
                </li>
              ) : null}
            </ul>
          </Panel>
        ) : null}
      </div>

      <CostsPanel />
    </>
  );
}
