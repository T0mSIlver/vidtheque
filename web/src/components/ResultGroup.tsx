import Link from "next/link";
import type { Hit } from "@/lib/api";
import { receipt } from "@/lib/format";
import { badges, type VideoGroup } from "@/lib/group";
import { Frame } from "./Frame";
import styles from "./ResultGroup.module.css";

// A card per video: header, then that video's moments, each ending in its
// receipt (demo-site.md §6.5). Three controls per row, never nested: the
// text links to the talk at the second, the receipt is the printed proof.
export function ResultGroup({ group }: { group: VideoGroup }) {
  return (
    <article className={styles.card}>
      <header className={styles.head}>
        <Link href={`/videos/${group.video_id}`} className={styles.cover}>
          <Frame src={group.thumb} alt="" />
        </Link>
        <div className={styles.headText}>
          <Link href={`/videos/${group.video_id}`} className={styles.title}>
            {group.title}
          </Link>
          <p className={styles.headMeta}>
            <span>{group.channel}</span>
            <span className={styles.id}>{group.video_id}</span>
            <span className={styles.id}>
              {group.hits.length} {group.hits.length === 1 ? "moment" : "moments"}
            </span>
          </p>
        </div>
      </header>
      <ol className={styles.moments}>
        {group.hits.map((hit) => (
          <Moment key={`${hit.source}-${hit.start}-${hit.frame_id ?? ""}`} hit={hit} />
        ))}
      </ol>
    </article>
  );
}

function Moment({ hit }: { hit: Hit }) {
  const kinds = badges(hit.source);
  const spokenOnly = kinds.length === 1 && kinds[0] === "spoken";
  const frameOnly = kinds.length === 1 && kinds[0] === "frame";
  return (
    <li className={styles.moment}>
      <span className={styles.time}>{hit.timestamp}</span>
      <span className={styles.badges}>
        {kinds.map((k) => (
          <span key={k} className={`${styles.badge} ${k === "on-screen" ? styles.seen : ""}`}>
            {k}
          </span>
        ))}
      </span>
      <a
        href={hit.link}
        rel="noreferrer"
        className={`${styles.text} ${frameOnly ? styles.muted : ""}`}
      >
        {spokenOnly && hit.text ? `“${hit.text}”` : hit.text}
      </a>
      <a href={hit.link} rel="noreferrer" className={styles.receipt}>
        {receipt(hit.link)}
      </a>
    </li>
  );
}
