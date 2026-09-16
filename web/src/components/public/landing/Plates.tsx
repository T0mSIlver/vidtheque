import { ASSETS, MOMENTS_BY_ID } from "./data/corpus";
import { hms } from "@/lib/format/landing";
import { PLATE_IDS, PLATE_TABS, type Box } from "./data/show";
import { EvidenceFrame } from "./EvidenceFrame";
import { Receipt } from "./Receipt";
import styles from "./landing.module.css";

export function Plates() {
  return (
    <div className={styles.plates}>
      {PLATE_IDS.map((id) => (
        <Plate key={id} id={id} />
      ))}
    </div>
  );
}

function Plate({ id }: { id: number }) {
  const m = MOMENTS_BY_ID[id];
  const cue = m.cues[m.cue_index];
  const tabs = PLATE_TABS[id] ?? {};
  const boxes: Box[] = m.ocr.map((line, i) => ({
    b: line.b,
    on: m.hero.includes(i),
    tab: tabs[i],
    conf: tabs[i] ? line.c.toFixed(2) : undefined,
  }));

  return (
    <div className={styles.plate}>
      <span className={styles.vlab}>
        still <u>nº{String(m.id).padStart(4, "0")}</u> · ord {m.ord} · {hms(m.t)} of{" "}
        {hms(m.duration)}
      </span>
      <div className={styles.th}>
        <EvidenceFrame
          src={ASSETS + m.img}
          alt={`Keyframe at ${hms(m.t)} of ${m.title} — ${m.speaker}`}
          boxes={boxes}
        />
      </div>
      <div className={styles.bd}>
        <div className={styles.who}>
          <strong>{m.speaker}</strong>
          <em>
            {m.title}
            {m.org ? ` · ${m.org}` : ""}
          </em>
        </div>
        <p className={styles.said}>{cue.t}</p>
        {m.ocr.length > 0 ? (
          <div className={styles.seenline}>
            <span className={styles.ll}>seen</span>
            <p>{m.hero.map((i) => m.ocr[i].t).join(" ")}</p>
          </div>
        ) : null}
        <div className={styles.mfoot}>
          <Receipt videoId={m.video_id} t={m.t} small />
          <span className={`${styles.label} ${styles.r}`}>{m.kick}</span>
        </div>
      </div>
    </div>
  );
}
