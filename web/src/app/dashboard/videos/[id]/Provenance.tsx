import { Pill } from "@/components/ui/Pill";
import type { Stage, VideoDetail } from "@/lib/dashboard/schemas";
import { at, bytes, count, DASH, duration, iso } from "@/lib/format";
import { table } from "@/components/dashboard/kit/table";
import { Figure, Panel, ui } from "@/components/dashboard/kit/ui";
import styles from "./detail.module.css";

/** What the pipeline stored for this video, and where its cues came from. */
export function Stored({
  counts,
  origins,
}: {
  counts: VideoDetail["counts"];
  origins: VideoDetail["cue_origins"];
}) {
  const sources = Object.entries(origins);
  return (
    <Panel id="counts" title="What was stored">
      <dl className={ui.figures}>
        <Figure
          label="cues"
          notes={[
            sources.length ? (
              <>
                {sources.map(([origin, n], index) => (
                  <span key={origin}>
                    {index ? " · " : ""}
                    {origin} {n}
                  </span>
                ))}
              </>
            ) : (
              <>none</>
            ),
          ]}
        >
          {count(counts.cues)}
        </Figure>
        <Figure label="chunks" notes={[<>from {count(counts.cues)} cues</>]}>
          {count(counts.chunks)}
        </Figure>
        <Figure label="keyframes" notes={[<>kept of {count(counts.keyframes)} captured</>]}>
          {count(counts.keyframes_kept)}
        </Figure>
        <Figure label="frames with text" notes={[<>{count(counts.ocr_lines)} lines read</>]}>
          {count(counts.ocr_frames)}
        </Figure>
        <Figure label="chapters" notes={[<>from the source metadata</>]}>
          {count(counts.chapters)}
        </Figure>
        <Figure
          label="keyframe bytes"
          notes={[
            counts.cues_with_words ? (
              <>word timings on {count(counts.cues_with_words)} cues</>
            ) : (
              <>no word timings stored</>
            ),
          ]}
        >
          {bytes(counts.jpeg_bytes)}
        </Figure>
      </dl>
    </Panel>
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
export function elapsed(start: number | null, finish: number | null): string {
  if (!start || !finish || finish < start) return DASH;
  return duration(finish - start);
}
