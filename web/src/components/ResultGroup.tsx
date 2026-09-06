import { Fragment } from "react";
import type { Hit } from "@/lib/api/schemas";
import { receipt } from "@/lib/format";
import { badges, channelWord, highlight, presentationOf, type VideoGroup } from "@/lib/group";
import { FrameShot } from "./Frame";
import styles from "./ResultGroup.module.css";

// A card per video: header, then that video's moments, each ending in its
// receipt (demo-site.md §6.5). Three controls per row, never nested: the
// thumbnail opens the frame full size, the text links to the talk at the
// second, and the receipt is the printed proof.
export function ResultGroup({ group, query = "" }: { group: VideoGroup; query?: string }) {
  // Source-agnostic: whichever moment of this video has a frame behind it. The
  // header says *which talk*, so it does not care which leg found it.
  const cover = group.hits.find((hit) => hit.thumb) ?? group.hits[0];
  const talk = videoUrl(cover.link);
  return (
    <article className={styles.card}>
      <header className={styles.head}>
        <div className={styles.cover}>
          <FrameShot shot={cover} alt="" label={channelWord(cover.source)} />
        </div>
        <div className={styles.headText}>
          {talk ? (
            <a href={talk} target="_blank" rel="noopener noreferrer" className={styles.title}>
              {group.title}
            </a>
          ) : (
            <span className={styles.title}>{group.title}</span>
          )}
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

// The video itself, not the moment: the same link with the `?t=` taken off
// (`app.js`'s `videoUrl`, restored 2026-09-07 when `/videos/{id}` went). A hit
// with no honest deep link has no honest video URL either, and gets a title
// that is text rather than a URL the page guessed.
function videoUrl(link: string): string | null {
  try {
    const url = new URL(link);
    url.search = "";
    return url.href;
  } catch {
    return null;
  }
}

// The snippet, presented as what it is evidence of (demo-site.md §6.3): speech
// in quotation marks, screen text in the mono face and lime, a visual match
// muted and never quoted, and the fusion of two channels as neither. A frame
// hit whose text the server dropped — there was none, the match was visual —
// gets no snippet at all rather than a sentence standing in for one.
const SNIPPET: Record<string, string> = {
  spoken: styles.snipSpoken,
  screen: styles.snipScreen,
  frame: styles.snipFrame,
  mixed: styles.snipMixed,
};

function Moment({ hit, query }: { hit: Hit; query: string }) {
  const kinds = badges(hit.source);
  const isFrame = hit.source === "frame";
  return (
    <li className={`${styles.moment} ${isFrame ? styles.isFrame : ""}`}>
      {/* A frame hit carries its own picture into the list: the image is the
          evidence, and the card's header frame is a different second. */}
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
      <a href={hit.link} target="_blank" rel="noopener noreferrer" className={styles.receipt}>
        {receipt(hit.link)}
      </a>
    </li>
  );
}

// The visitor's own words, marked inside the corpus's sentence. Runs of text
// and `<mark>`, never markup built from either: the query is whatever was typed
// and the snippet is whatever was on somebody's screen.
export function Marked({ text, query }: { text: string; query: string }) {
  return (
    <>
      {highlight(text, query).map((run, i) =>
        run.hit ? <mark key={i}>{run.text}</mark> : <Fragment key={i}>{run.text}</Fragment>,
      )}
    </>
  );
}
