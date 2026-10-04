import Link from "next/link";
import { Score } from "@/components/feed/parts";
import styles from "@/components/feed/feed.module.css";
import { FEED } from "@/lib/dashboard/client";
import type { WeekPick } from "@/lib/dashboard/schemas";
import { asked } from "@/lib/format";

// Claude's picks open the week (companion.md §6.4): its own choices on top of
// the verdicts, each with its reason, outside the budget.

export function ClaudePicks({ picks }: { picks: WeekPick[] }) {
  if (picks.length === 0) return null;
  return (
    <section aria-labelledby="claude-picks" className={styles.picksBand}>
      <h2 className={styles.label} id="claude-picks">
        Claude&rsquo;s picks
      </h2>
      <ol className={styles.rows}>
        {picks.map((pick) => (
          <li key={pick.video_id}>
            <Link className={styles.row} href={`${FEED}/${encodeURIComponent(pick.video_id)}`}>
              {pick.channel ? <span className={styles.channel}>{pick.channel}</span> : null}
              <span className={styles.title}>{pick.title || pick.video_id}</span>
              <span className={styles.meta}>
                {pick.score !== null ? <Score score={pick.score} /> : null}
                <span className={styles.duration}>{asked(pick.moments_s, pick.duration_s)}</span>
              </span>
              <span className={styles.reason}>{pick.reason}</span>
            </Link>
          </li>
        ))}
      </ol>
    </section>
  );
}
