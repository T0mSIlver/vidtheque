import { Pill } from "@/components/Pill";
import { ROOT } from "@/lib/dashboard/client";
import type { IndexOutcome } from "@/lib/dashboard/schemas";
import controls from "../kit/controls.module.css";
import { notice } from "../kit/notice";
import { table } from "../kit/table";
import { DashLink, ui } from "../kit/ui";
import styles from "./index.module.css";

/** The two ids the "already indexed" line prints before "and more". */
const ALREADY_SHOWN = 10;

/**
 * What a submission did: the jobs it made, what it left alone, and every
 * refusal with the batch of URLs it was refused for. A `409` lands here too.
 */
export function Receipt({ outcome, perJob }: { outcome: IndexOutcome; perJob: number }) {
  const shown = outcome.already_indexed.slice(0, ALREADY_SHOWN);
  return (
    <section className={ui.panel} aria-labelledby="queued" role="status">
      <h2 className={ui.panelTitle} id="queued">
        What that submission did
      </h2>
      <p className={notice.panelNote}>
        <span className={ui.mono}>{outcome.urls}</span> URL(s){" "}
        {outcome.batches > 1 ? (
          <>
            split into <span className={ui.mono}>{outcome.batches}</span> jobs of at most{" "}
            <span className={ui.mono}>{perJob}</span>.
          </>
        ) : (
          <>in one job.</>
        )}
      </p>

      {outcome.jobs.length ? (
        <ul className={styles.jobs}>
          {outcome.jobs.map((job) => (
            <li key={job.job_id}>
              <DashLink className={ui.mono} href={`${ROOT}/jobs/${encodeURIComponent(job.job_id)}`}>
                {job.job_id}
              </DashLink>
              <span className={ui.rowMeta}>
                <span className={ui.mono}>{job.items}</span> video(s) queued
              </span>
            </li>
          ))}
        </ul>
      ) : null}

      {outcome.already_indexed.length ? (
        <>
          <p className={styles.lead}>
            <span className={ui.mono}>{outcome.already_indexed.length}</span> already indexed and
            left alone.
          </p>
          <p className={styles.note}>
            {shown.map((videoId, index) => (
              <span key={videoId}>
                <DashLink
                  className={ui.mono}
                  href={`${ROOT}/videos/${encodeURIComponent(videoId)}`}
                >
                  {videoId}
                </DashLink>
                {index < shown.length - 1 ? ", " : ""}
              </span>
            ))}
            {outcome.already_indexed.length > ALREADY_SHOWN ? " and more" : ""}. Tick{" "}
            <em>force re-index</em> to rebuild one.
          </p>
        </>
      ) : null}

      {outcome.errors.map((error, index) => (
        <div key={index}>
          <p className={styles.refusal}>
            <Pill state={error.error ?? "E_HTTP"} tone="bad" />
            <span>{error.message}</span>
          </p>
          <p className={styles.note}>
            {error.urls.map((url) => (
              <span key={url}>
                <code>{url}</code>{" "}
              </span>
            ))}
            {error.next ? (
              <>
                <br />
                {error.next}
              </>
            ) : null}
          </p>
        </div>
      ))}

      {outcome.jobs.length ? (
        <p className={table.pager}>
          <DashLink className={controls.ghostlink} href={`${ROOT}/jobs?state=active`}>
            Watch the queue
          </DashLink>
        </p>
      ) : null}
    </section>
  );
}
