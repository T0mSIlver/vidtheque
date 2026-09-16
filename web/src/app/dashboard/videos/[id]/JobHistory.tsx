import { Pill } from "@/components/ui/Pill";
import { ROOT } from "@/lib/dashboard/client";
import type { VideoDetail } from "@/lib/dashboard/schemas";
import { at, count, DASH } from "@/lib/format";
import { table } from "../../kit/table";
import { DashLink, Panel, ui } from "../../kit/ui";
import styles from "./detail.module.css";

export function JobHistory({ history }: { history: VideoDetail["job_history"] }) {
  if (!history.jobs.length) {
    return (
      <Panel id="index-history" title="Recent indexing runs">
        <p className={ui.emptyNote}>No indexing job is linked to this video.</p>
      </Panel>
    );
  }
  const dash = <span className={styles.muted}>{DASH}</span>;
  return (
    <Panel id="index-history" title="Recent indexing runs">
      <div className={table.tablewrap}>
        <table className={table.grid}>
          <caption className={ui.srOnly}>The latest jobs that touched this video</caption>
          <thead>
            <tr>
              <th scope="col">job</th>
              <th scope="col">state</th>
              <th scope="col">kind</th>
              <th scope="col">created</th>
              <th scope="col">finished</th>
              <th scope="col">error</th>
              <th scope="col">degraded stages</th>
            </tr>
          </thead>
          <tbody>
            {history.jobs.map((job) => (
              <tr key={job.job_id}>
                <th scope="row">
                  <DashLink href={`${ROOT}/jobs/${job.job_id}`}>
                    <code>{job.job_id}</code>
                  </DashLink>
                </th>
                <td>
                  <Pill state={job.state} />
                </td>
                <td>
                  <code>{job.kind}</code>
                </td>
                <td>{at(job.created_at)}</td>
                <td>{job.finished_at ? at(job.finished_at) : dash}</td>
                <td>{job.error_code ? <code>{job.error_code}</code> : dash}</td>
                <td>
                  {job.degraded_stages.length
                    ? job.degraded_stages.map((stage, index) => (
                        <span key={stage}>
                          <code>{stage}</code>
                          {index < job.degraded_stages.length - 1 ? ", " : ""}
                        </span>
                      ))
                    : dash}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className={styles.panelNote}>Latest {count(history.cap)} at most; no total is computed.</p>
    </Panel>
  );
}
