import type { FeedItem } from "@/lib/api";
import { clock, minutes } from "@/lib/format";
import { scoreWord } from "@/lib/feed/words";
import styles from "./feed.module.css";

/** One verdict as the public pages show it: the call, why, and up to three
 *  moments, each a short attributed quote that links to its second on YouTube
 *  (demo-site.md §8.3). `compact` drops the summary, for the landing. */
export function Recommendation({ item, compact = false }: { item: FeedItem; compact?: boolean }) {
  return (
    <article className={styles.card}>
      <p className={styles.head}>
        <span className={styles.score} data-score={item.score}>
          {scoreWord(item.score)}
        </span>
        <span className={styles.meta}>
          {item.channel} · {minutes(item.duration_s)}
        </span>
      </p>
      <h3 className={styles.title}>
        {item.url ? (
          <a href={item.url} rel="noopener">
            {item.title}
          </a>
        ) : (
          item.title
        )}
      </h3>
      <p className={styles.reason}>{item.reason}</p>
      {compact ? null : <p className={styles.summary}>{item.summary}</p>}
      {item.matches.length ? (
        <ul className={styles.matches} aria-label="Matches the sample profile">
          {item.matches.map((match) => (
            <li key={match.text} className={styles.chip} data-direction={match.direction}>
              <span aria-hidden="true">{match.direction === "up" ? "↑" : "↓"}</span>
              <span className={styles.visuallyHidden}>
                {match.direction === "up" ? "more of" : "less of"}{" "}
              </span>
              {match.text}
            </li>
          ))}
        </ul>
      ) : null}
      {item.moments.length ? (
        <ol className={styles.moments}>
          {item.moments.slice(0, compact ? 1 : 3).map((moment) => (
            <li key={moment.offset_s}>
              {moment.url ? (
                <a className={styles.at} href={moment.url} rel="noopener">
                  {clock(moment.offset_s)}
                </a>
              ) : (
                <span className={styles.at}>{clock(moment.offset_s)}</span>
              )}
              <div>
                <p className={styles.why}>{moment.why}</p>
                {moment.excerpt ? (
                  <blockquote className={styles.quote} cite={moment.url ?? undefined}>
                    <p className={styles.quoteText}>“{moment.excerpt}”</p>
                    {moment.speaker ? (
                      <footer className={styles.quoteWho}>{moment.speaker}</footer>
                    ) : null}
                  </blockquote>
                ) : null}
              </div>
            </li>
          ))}
        </ol>
      ) : null}
    </article>
  );
}
