import Link from "next/link";
import { RetryIn } from "@/components/ui/RetryIn";
import type { FeedOutcome } from "@/lib/api/search";
import { Recommendation } from "./Recommendation";
import styles from "./feed.module.css";

export const PAGE = 10;

/** The profile the feed was scored against, then the feed, then the next page. */
export function SampleFeed({
  outcome,
  offset,
  page,
}: {
  outcome: FeedOutcome;
  offset: number;
  /** The page's URL at another offset of the feed. */
  page: (offset: number) => string;
}) {
  if (outcome.kind === "rate_limited") {
    return <RetryIn seconds={outcome.retryAfter} message="Too many requests for now." />;
  }
  if (outcome.kind === "unreachable") {
    return (
      <p className={styles.empty}>The sample feed is unavailable right now. Reload in a minute.</p>
    );
  }
  const { profile, items, next_offset } = outcome.feed;
  return (
    <div className={styles.feed}>
      {profile ? (
        <section className={styles.profile} aria-labelledby="sample-profile">
          <h2 className={styles.profileHead} id="sample-profile">
            Scored for: {profile.name}
          </h2>
          <ul className={styles.matches}>
            {profile.entries.map((entry) => (
              <li
                key={entry.text}
                className={styles.chip}
                data-direction={entry.weight >= 0 ? "up" : "down"}
              >
                <span aria-hidden="true">{entry.weight >= 0 ? "↑" : "↓"}</span>
                <span className={styles.visuallyHidden}>
                  {entry.weight >= 0 ? "more of" : "less of"}{" "}
                </span>
                {entry.text}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {items.length ? (
        <ol className={styles.list}>
          {items.map((item) => (
            <li key={item.video_id}>
              <Recommendation item={item} />
            </li>
          ))}
        </ol>
      ) : (
        <p className={styles.empty}>No recommendation here yet.</p>
      )}
      {offset > 0 || next_offset !== null ? (
        <nav className={styles.pages} aria-label="Sample feed pages">
          {offset > 0 ? (
            <Link className={styles.more} href={page(Math.max(0, offset - PAGE))}>
              Previous
            </Link>
          ) : null}
          {next_offset !== null ? (
            <Link className={styles.more} href={page(next_offset)}>
              Next recommendations
            </Link>
          ) : null}
        </nav>
      ) : null}
    </div>
  );
}
