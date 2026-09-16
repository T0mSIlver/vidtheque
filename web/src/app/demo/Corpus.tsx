import type { Video } from "@/lib/api/schemas";
import { readCorpus } from "@/lib/search";
import styles from "./page.module.css";

/** What is in the corpus, under the cold page's examples. */
export async function CorpusPanel() {
  return <Corpus videos={await readCorpus()} />;
}

export function Corpus({ videos }: { videos: Video[] }) {
  if (!videos.length) return null;
  return (
    <div className={styles.corpus}>
      <p className={styles.phead}>in this corpus</p>
      <ul>
        {videos.map((video) => (
          <li key={video.video_id}>
            <a href={video.link} target="_blank" rel="noopener noreferrer">
              {video.title}
            </a>
            {video.channel ? <span className={styles.who}>{video.channel}</span> : null}
          </li>
        ))}
      </ul>
    </div>
  );
}
