"use client";

import Link from "next/link";
import { useState } from "react";
import { FeedFailure, Outside, Score } from "@/components/feed/parts";
import styles from "@/components/feed/feed.module.css";
import { dashboard, FEED } from "@/lib/dashboard/client";
import { useResource } from "@/lib/dashboard/resource";
import type { Feed, FeedItem } from "@/lib/dashboard/schemas";
import { clock, count } from "@/lib/format";

// What should I watch? Verdicts newest first, 2–3 on top, 0–1 folded into
// "skipped (n)" (companion.md §6). Pages append; `has_more` decides the button.

type BandName = "top" | "skipped";

export function FeedView() {
  const [skippedOpen, setSkippedOpen] = useState(false);
  const first = useBandPage("top", 0);

  if (first.error && !first.data) {
    return <FeedFailure error={first.error} onRetry={first.reload} />;
  }
  const skipped = first.data?.skipped;

  return (
    <>
      <section aria-labelledby="to-watch">
        <h1 className={styles.label} id="to-watch">
          To watch
        </h1>
        <Band band="top" />
      </section>

      {skipped && skipped.count > 0 ? (
        <section className={styles.skipped} aria-labelledby="skipped">
          <h2 id="skipped">
            <button
              className={styles.fold}
              type="button"
              aria-expanded={skippedOpen}
              onClick={() => setSkippedOpen((open) => !open)}
            >
              <span>
                Skipped ({count(skipped.count)}
                {skipped.capped ? "+" : ""})
              </span>
              <span aria-hidden="true">{skippedOpen ? "−" : "+"}</span>
            </button>
          </h2>
          {skippedOpen ? <Band band="skipped" /> : null}
        </section>
      ) : null}
    </>
  );
}

function useBandPage(band: BandName, offset: number) {
  return useResource<Feed>(`feed:${band}:${offset}`, (signal) =>
    dashboard.feed(new URLSearchParams({ band, offset: String(offset) }), signal),
  );
}

/** One band as the pages read so far. */
function Band({ band }: { band: BandName }) {
  const [offsets, setOffsets] = useState([0]);
  return (
    <ol className={styles.rows}>
      {offsets.map((offset, index) => (
        <BandPage
          key={offset}
          band={band}
          offset={offset}
          first={index === 0}
          onMore={index === offsets.length - 1 ? (next) => setOffsets([...offsets, next]) : null}
        />
      ))}
    </ol>
  );
}

function BandPage({
  band,
  offset,
  first,
  onMore,
}: {
  band: BandName;
  offset: number;
  first: boolean;
  onMore: ((next: number) => void) | null;
}) {
  const page = useBandPage(band, offset);
  if (!page.data) {
    if (page.error) {
      return (
        <li className={styles.rowNote}>
          <FeedFailure error={page.error} onRetry={page.reload} />
        </li>
      );
    }
    return <li className={styles.pending} aria-busy="true" />;
  }
  const { items, pagination } = page.data;
  const next = pagination.next_offset;
  return (
    <>
      {first && items.length === 0 ? (
        <li className={styles.rowNote}>
          {band === "top"
            ? "Nothing to watch yet. A verdict is written once a new video from the channels you follow is indexed."
            : "Nothing skipped."}
        </li>
      ) : null}
      {items.map((item) => (
        <Row key={item.video_id} item={item} />
      ))}
      {onMore && pagination.has_more && next !== null ? (
        <li className={styles.rowNote}>
          <button className={styles.action} type="button" onClick={() => onMore(next)}>
            More
          </button>
        </li>
      ) : null}
    </>
  );
}

function Row({ item }: { item: FeedItem }) {
  return (
    <li>
      <Link className={styles.row} href={`${FEED}/${encodeURIComponent(item.video_id)}`}>
        {item.channel ? <span className={styles.channel}>{item.channel}</span> : null}
        <span className={styles.title}>{item.title || item.video_id}</span>
        <span className={styles.meta}>
          <Score score={item.score} />
          {item.explored ? <Outside /> : null}
          <span className={styles.duration}>{clock(item.duration_s)}</span>
        </span>
        {item.reason ? <span className={styles.reason}>{item.reason}</span> : null}
      </Link>
    </li>
  );
}
