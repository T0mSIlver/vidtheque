"use client";

import { Pill } from "@/components/ui/Pill";
import { ROOT } from "@/lib/dashboard/client";
import type { JobDetail, JobEvent, JobItem } from "@/lib/dashboard/schemas";
import { at, DASH, duration, hms, iso } from "@/lib/format";
import { notice } from "@/components/dashboard/kit/notice";
import { table } from "@/components/dashboard/kit/table";
import { DashLink, Panel, Sep, ui } from "@/components/dashboard/kit/ui";
import { useArrivals } from "@/components/dashboard/polling";
import styles from "../jobs.module.css";

// The lower panels of a job's page: items, the stages in focus, the degraded
// list and the event log.

/** `views.EVENT_PREVIEW`: a rendering bound, not a fetch bound. */
const EVENT_PREVIEW = 8;

export function Items({ data, redacted }: { data: JobDetail; redacted: boolean }) {
  return (
    <Panel id="items" title="Items">
      {data.items.length ? (
        <div className={table.tablewrap}>
          <table className={`${table.grid} ${styles.items}`}>
            <caption className={ui.srOnly}>
              Each video in this job, its state and its retry counter
            </caption>
            <thead>
              <tr>
                <th scope="col" className={table.num}>
                  #
                </th>
                <th scope="col">video</th>
                <th scope="col">state</th>
                <th scope="col">stage</th>
                <th scope="col" className={table.num}>
                  attempts
                </th>
                <th scope="col" className={table.num}>
                  took
                </th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((item) => (
                <ItemRows key={item.item_id} item={item} redacted={redacted} />
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className={ui.emptyNote}>This job has no items.</p>
      )}
      {data.items_capped ? (
        <p className={notice.panelNote}>Only the first {data.items.length} items are listed.</p>
      ) : null}
    </Panel>
  );
}

function ItemRows({ item, redacted }: { item: JobItem; redacted: boolean }) {
  return (
    <>
      <tr className={item.state === "failed" ? styles.bad : undefined}>
        <td className={table.num} data-label="#">
          {item.seq}
        </td>
        <th scope="row" className={styles.colJob} data-label="Video">
          {item.video_id ? (
            <>
              <DashLink
                className={ui.rowTitle}
                href={`${ROOT}/videos/${encodeURIComponent(item.video_id)}`}
              >
                {item.title || item.video_id}
              </DashLink>
              <span className={ui.rowMeta}>
                {item.channel ? (
                  <>
                    {item.channel}
                    <Sep />{" "}
                  </>
                ) : null}
                {item.duration_s ? (
                  <>
                    {hms(item.duration_s)}
                    <Sep />{" "}
                  </>
                ) : null}
                <code>{item.video_id}</code>
              </span>
            </>
          ) : item.source_url ? (
            <>
              <span className={ui.rowTitle}>{item.source_url}</span>
              <span className={ui.rowMeta}>never resolved to a video</span>
            </>
          ) : (
            <>
              <span className={`${ui.rowTitle} ${ui.muted}`}>a submitted URL</span>
              <span className={ui.rowMeta}>never resolved to a video, and not published here</span>
            </>
          )}
        </th>
        <td data-label="State">
          <Pill state={item.state} />
        </td>
        <td data-label="Stage">
          {item.stage ? (
            <code>
              {item.stage} {item.stage_pct}%
            </code>
          ) : (
            <span className={ui.muted}>{DASH}</span>
          )}
        </td>
        <td className={table.num} data-label="Attempts">
          {item.attempts}/{item.max_attempts}
        </td>
        <td className={table.num} data-label="Took">
          {duration(item.took_s)}
        </td>
      </tr>
      {item.error_code ? (
        <tr className={styles.rowError}>
          <td colSpan={6}>
            <span className={styles.label}>{item.error_code}</span>{" "}
            {item.error_message ? (
              <span className={styles.errText}>{item.error_message}</span>
            ) : redacted ? (
              <span className={styles.errText}>message not published on this instance</span>
            ) : null}
            {item.state === "queued" && item.retries_left ? (
              <span className={ui.muted}>
                {" "}
                · {item.retries_left} attempt(s) left, so it is queued to try again
              </span>
            ) : null}
          </td>
        </tr>
      ) : null}
    </>
  );
}

/** The seven stages of the one item in focus, never every item's (§6.3). */
export function Stages({ data }: { data: JobDetail }) {
  const stages = data.stages;
  const focus = data.focus ?? null;
  if (!focus || !stages?.length) return null;
  return (
    <Panel id="focus" subject={focus.title || focus.video_id || null} title="Stage by stage">
      <div className={table.tablewrap}>
        <table className={table.grid}>
          <caption className={ui.srOnly}>
            The seven pipeline stages for the item this job is on
          </caption>
          <thead>
            <tr>
              <th scope="col">stage</th>
              <th scope="col">state</th>
              <th scope="col">started</th>
              <th scope="col" className={table.num}>
                took
              </th>
            </tr>
          </thead>
          <tbody>
            {stages.map((stage) => (
              <tr key={stage.stage} className={stage.state === "failed" ? styles.bad : undefined}>
                <th scope="row">
                  <code>{stage.stage}</code>
                </th>
                <td>
                  <Pill state={stage.state} />
                </td>
                <td>
                  {stage.started_at ? (
                    <time dateTime={iso(stage.started_at)}>{at(stage.started_at)}</time>
                  ) : (
                    <span className={ui.muted}>{DASH}</span>
                  )}
                </td>
                <td className={table.num}>{duration(stage.took_s)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {focus.video_id ? (
        <p className={notice.panelNote}>
          <DashLink href={`${ROOT}/videos/${encodeURIComponent(focus.video_id)}`}>
            This item&rsquo;s video page
          </DashLink>
          . Every other item&rsquo;s stages are on its own.
        </p>
      ) : null}
    </Panel>
  );
}

/** `done` with a stage missing underneath. */
export function Degraded({ data }: { data: JobDetail }) {
  const rows = data.degraded;
  if (!rows?.length) return null;
  return (
    <Panel id="degraded" title="Finished with something missing">
      <ul className={styles.rowlist}>
        {rows.map((entry) => (
          <li className={styles.minirow} key={`${entry.seq}-${entry.stage}`}>
            <span>
              {entry.video_id ? (
                <DashLink href={`${ROOT}/videos/${encodeURIComponent(entry.video_id)}`}>
                  <code>{entry.video_id}</code>
                </DashLink>
              ) : (
                <code className={ui.muted}>item {entry.seq}</code>
              )}
              <Sep /> <code>{entry.stage}</code> failed
            </span>
            {entry.error ? <span className={styles.errText}>{entry.error}</span> : null}
          </li>
        ))}
      </ul>
      <p className={notice.panelNote}>
        Only <code>fetch</code> and <code>stt</code> are essential, so these count as{" "}
        <code>done</code>. Re-index to get the missing channel back.
      </p>
    </Panel>
  );
}

/** Newest first, a bounded preview and the rest behind an expander. An entry
 *  that landed while the page was open is marked. */
export function Events({ events, redacted }: { events: JobEvent[]; redacted: boolean }) {
  const arrived = useArrivals(events, (event) => event.id);
  const preview = events.slice(0, EVENT_PREVIEW);
  const older = events.slice(EVENT_PREVIEW);
  return (
    <Panel id="events" title="Event log">
      {events.length ? (
        <>
          <ol className={styles.events}>
            {preview.map((event) => (
              <Event event={event} key={event.id} isNew={arrived.has(event.id)} />
            ))}
          </ol>
          {older.length ? (
            <details className={styles.digest}>
              <summary>
                <span className={styles.digestCount}>{older.length}</span> older event(s)
              </summary>
              <ol className={styles.events}>
                {older.map((event) => (
                  <Event event={event} key={event.id} isNew={arrived.has(event.id)} />
                ))}
              </ol>
            </details>
          ) : null}
        </>
      ) : (
        <p className={ui.emptyNote}>Nothing has been logged for this job.</p>
      )}
      <p className={notice.panelNote}>
        Newest first, {events.length} shown.
        {redacted ? " Message text is not published on this instance." : null}
      </p>
    </Panel>
  );
}

function Event({ event, isNew }: { event: JobEvent; isNew?: boolean }) {
  return (
    <li className={`${styles.event} ${isNew ? styles.isNew : ""}`} data-new={isNew || undefined}>
      <span className={styles.eventAt}>
        <time dateTime={iso(event.at)}>{at(event.at)}</time>
      </span>
      <Pill state={event.level} />
      {event.stage ? <code>{event.stage}</code> : null}
      <span className={`${styles.eventText} ${event.message ? "" : ui.muted}`}>
        {event.message || "message not published on this instance"}
      </span>
    </li>
  );
}
