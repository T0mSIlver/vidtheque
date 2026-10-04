import { Pill } from "@/components/ui/Pill";
import type { Stage, VideoDetail } from "@/lib/dashboard/schemas";
import { at, bytes, count, DASH, duration, iso } from "@/lib/format";
import { table } from "@/components/dashboard/kit/table";
import { Fold } from "@/components/dashboard/kit/Fold";
import { Panel, ui } from "@/components/dashboard/kit/ui";
import styles from "./detail.module.css";

/** What the pipeline stored for this video, folded: a figure per count and
 *  no sentence under any of them (§29.3). */
export function Stored({
  counts,
  origins,
  transcript,
}: {
  counts: VideoDetail["counts"];
  origins: VideoDetail["cue_origins"];
  transcript: VideoDetail["transcript"];
}) {
  const figures: [string, string][] = [
    ["cues", count(counts.cues)],
    ...Object.entries(origins).map(([origin, n]): [string, string] => [
      `cues from ${origin}`,
      count(n),
    ]),
    ["cues with word timings", count(counts.cues_with_words)],
    ["words", count(transcript.words)],
    ["characters", count(transcript.chars)],
    ["chunks", count(counts.chunks)],
    ["keyframes captured", count(counts.keyframes)],
    ["keyframes kept", count(counts.keyframes_kept)],
    ["frames with text", count(counts.ocr_frames)],
    ["on-screen lines", count(counts.ocr_lines)],
    ["chapters", count(counts.chapters)],
    ["keyframe bytes", bytes(counts.jpeg_bytes)],
  ];
  return (
    <section className={styles.stats} aria-label="Statistics">
      <Fold label="statistics">
        <dl className={styles.statlist}>
          {figures.map(([label, value]) => (
            <div key={label}>
              <dt className={styles.statLabel}>{label}</dt>
              <dd className={styles.statValue}>{value}</dd>
            </div>
          ))}
        </dl>
      </Fold>
    </section>
  );
}

/**
 * The seven stages as they are; `absent` never ran and recedes. The model
 * column stays on the projection, every cell a dash, so the redaction is
 * visible and the table keeps its shape (§2.4).
 */
export function Provenance({ stages }: { stages: Stage[] }) {
  return (
    <Panel id="provenance" title="Provenance">
      <div className={table.tablewrap}>
        <table className={`${table.grid} ${styles.stages}`}>
          <caption className={ui.srOnly}>
            Each pipeline stage, its state and the model that produced it
          </caption>
          <thead>
            <tr>
              <th scope="col">stage</th>
              <th scope="col">state</th>
              <th scope="col">model</th>
              <th scope="col">started</th>
              <th scope="col" className={table.num}>
                took
              </th>
            </tr>
          </thead>
          <tbody>
            {stages.map((stage) => (
              <StageRows key={stage.stage} stage={stage} />
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function StageRows({ stage }: { stage: Stage }) {
  const tone =
    stage.state === "failed" ? styles.bad : stage.state === "absent" ? styles.absent : "";
  return (
    <>
      <tr className={tone}>
        <th scope="row">
          <code>{stage.stage}</code>
        </th>
        <td>
          <Pill state={stage.state} />
        </td>
        {/* `model_key` records what succeeded, and is `null` in the projection. */}
        <td className={styles.colModel}>
          {stage.model_key ? (
            <code>{stage.model_key}</code>
          ) : (
            <span className={styles.muted}>{DASH}</span>
          )}
        </td>
        <td>
          {stage.started_at ? (
            <time dateTime={iso(stage.started_at)}>{at(stage.started_at)}</time>
          ) : (
            <span className={styles.muted}>{DASH}</span>
          )}
        </td>
        <td className={table.num}>{elapsed(stage.started_at, stage.finished_at)}</td>
      </tr>
      {stage.error ? (
        <tr className={styles.stageError}>
          <td colSpan={5}>
            <span className={styles.errLabel}>error</span>{" "}
            <span className={styles.errText}>{stage.error}</span>
          </td>
        </tr>
      ) : null}
    </>
  );
}

/** How long a stage took, or the dash when either clock is missing: a failed
 *  stage has a start and no finish (§4.1). */
function elapsed(start: number | null, finish: number | null): string {
  if (!start || !finish || finish < start) return DASH;
  return duration(finish - start);
}
