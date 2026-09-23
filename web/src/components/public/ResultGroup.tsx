import { Fragment } from "react";
import type { Hit } from "@/lib/api/schemas";
import {
  badgeWords,
  channelWord,
  highlight,
  presentationOf,
  type VideoGroup,
} from "@/lib/api/group";
import { FrameShot } from "./Frame";
import { Receipt } from "./Receipt";
import styles from "./ResultGroup.module.css";

// A card per video, then its moments, each ending in its receipt (demo-site.md
// §6.5). Three sibling controls per row, never nested: thumbnail, text, receipt.
export function ResultGroup({ group, query = "" }: { group: VideoGroup; query?: string }) {
  // A frame that matched first; a spoken moment's is only what was on screen then.
  const cover =
    group.hits.find((hit) => hit.thumb && hit.source !== "transcript") ??
    group.hits.find((hit) => hit.thumb) ??
    group.hits[0];
  const talk = videoUrl(cover.link);
  return (
    <article className={styles.card}>
      <header className={styles.head}>
        <div className={styles.cover}>
          <FrameShot shot={cover} alt="" label={channelWord(cover.source)} />
        </div>
        <div className={styles.headText}>
          <h3 className={styles.title}>
            {talk ? (
              <a href={talk} target="_blank" rel="noopener noreferrer">
                {group.title}
              </a>
            ) : (
              group.title
            )}
          </h3>
          <p className={styles.headMeta}>
            <span>{group.channel}</span>
            <span className={styles.id}>
              {group.hits.length} {group.hits.length === 1 ? "moment" : "moments"}
            </span>
            <span className={styles.id}>{group.video_id}</span>
          </p>
        </div>
      </header>
      <ol className={styles.moments}>
        {group.hits.map((hit) => (
          <Moment
            key={`${hit.source}-${hit.start}-${hit.frame_id ?? ""}`}
            hit={hit}
            query={query}
          />
        ))}
      </ol>
    </article>
  );
}

// The video, not the moment: the link without `?t=`. No honest link, no URL.
function videoUrl(link: string): string | null {
  try {
    const url = new URL(link);
    url.search = "";
    return url.href;
  } catch {
    return null;
  }
}

// The snippet, presented as what it is evidence of (demo-site.md §6.3).
const SNIPPET: Record<string, string> = {
  spoken: styles.snipSpoken,
  screen: styles.snipScreen,
  frame: styles.snipFrame,
  mixed: styles.snipMixed,
};

function Moment({ hit, query }: { hit: Hit; query: string }) {
  const kinds = badgeWords(hit.source);
  const isFrame = hit.source === "frame";
  return (
    <li className={`${styles.moment} ${isFrame ? styles.isFrame : ""}`}>
      {isFrame && hit.thumb ? (
        <div className={styles.momentShot}>
          <FrameShot shot={hit} alt="" label={channelWord(hit.source)} />
        </div>
      ) : null}
      <a href={hit.link} target="_blank" rel="noopener noreferrer" className={styles.momentLink}>
        <span className={styles.time}>{hit.timestamp}</span>
        {kinds.length > 0 ? (
          <span className={styles.badges}>
            {kinds.map((kind) => (
              <span
                key={kind}
                className={`${styles.badge} ${kind === "on-screen" ? styles.seen : ""} ${
                  kind === "frame" ? styles.frameBadge : ""
                }`}
              >
                {kind}
              </span>
            ))}
          </span>
        ) : null}
        {hit.text ? (
          <span className={`${styles.snip} ${SNIPPET[presentationOf(hit.source)]}`}>
            <Marked text={hit.text} query={query} />
          </span>
        ) : null}
      </a>
      <Receipt href={hit.link} className={styles.receipt} />
    </li>
  );
}

// The query's words marked in the snippet: text runs and `<mark>`, never markup
// built from either.
function Marked({ text, query }: { text: string; query: string }) {
  return (
    <>
      {highlight(text, query).map((run, i) =>
        run.hit ? <mark key={i}>{run.text}</mark> : <Fragment key={i}>{run.text}</Fragment>,
      )}
    </>
  );
}
