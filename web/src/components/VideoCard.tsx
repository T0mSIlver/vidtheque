import Link from "next/link";
import type { Video } from "@/lib/api";
import { Frame } from "./Frame";
import { Pill } from "./Pill";
import styles from "./VideoCard.module.css";

export function VideoCard({ video, priority = false }: { video: Video; priority?: boolean }) {
  return (
    <li className={styles.item}>
      <Link href={`/videos/${video.video_id}`} className={styles.link}>
        <Frame src={video.thumb} alt="" priority={priority} />
        <span className={styles.title}>{video.title}</span>
        <span className={styles.meta}>
          <span className={styles.channel}>{video.channel}</span>
          <span className={styles.machine}>
            {video.duration} · {video.published}
          </span>
          {video.index_state !== "ready" ? <Pill state={video.index_state} /> : null}
        </span>
      </Link>
    </li>
  );
}
