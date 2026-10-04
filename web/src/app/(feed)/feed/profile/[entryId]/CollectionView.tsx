"use client";

import Link from "next/link";
import { FeedFailure } from "@/components/feed/parts";
import styles from "@/components/feed/feed.module.css";
import { Title } from "@/components/dashboard/kit/ui";
import { dashboard, FEED } from "@/lib/dashboard/client";
import { useResource } from "@/lib/dashboard/resource";
import type { Collection } from "@/lib/dashboard/schemas";
import { clock, minutes } from "@/lib/format";
import { repeatNote } from "@/lib/feed/words";

// The best moments on one interest, across videos, best first (companion.md
// §6.2). Read from the top: a moment that says again what one above it said
// opens after the repeat. A list that ends; nothing plays on its own (§8).

export function CollectionView({ entryId }: { entryId: number }) {
  const collection = useResource<Collection>(`collection:${entryId}`, (signal) =>
    dashboard.collection(entryId, signal),
  );
  if (!collection.data) {
    if (collection.error) {
      return <FeedFailure error={collection.error} onRetry={collection.reload} />;
    }
    return <div className={styles.pending} aria-busy="true" />;
  }
  return <Loaded collection={collection.data} />;
}

function Loaded({ collection }: { collection: Collection }) {
  const { entry, moments } = collection;
  return (
    <>
      <Title>{entry.text}</Title>
      <article className={styles.video}>
        <header className={styles.videoHead}>
          <Link className={styles.channel} href={`${FEED}/profile`}>
            ‹ Your interests
          </Link>
          <h1 className={styles.videoTitle}>{entry.text}</h1>
          <p className={styles.meta}>
            <span className={styles.duration}>
              {moments.length === 1 ? "1 moment" : `${moments.length} moments`},{" "}
              {minutes(collection.moments_s)}
            </span>
          </p>
        </header>

        <ol className={styles.moments}>
          {moments.map((moment, index) => (
            <li key={`${moment.video.video_id}:${moment.offset_s}`}>
              <Link
                className={styles.source}
                href={`${FEED}/${encodeURIComponent(moment.video.video_id)}`}
              >
                {index + 1}. {moment.video.channel ? `${moment.video.channel} · ` : ""}
                {moment.video.title || moment.video.video_id}
              </Link>
              <a
                className={styles.moment}
                href={moment.url}
                target="_blank"
                rel="noopener noreferrer"
                onClick={() =>
                  void dashboard
                    .signal("watch", moment.video.video_id, moment.start_s)
                    .catch(() => {})
                }
              >
                <span className={styles.timecode}>
                  {clock(moment.start_s)}–{clock(moment.end_s)}
                </span>
                <span className={styles.momentText}>
                  <span className={styles.why}>{moment.why}</span>
                  {moment.repeat ? (
                    <span className={styles.repeat}>
                      {repeatNote(
                        `moment ${moment.repeat.item + 1}`,
                        moment.repeat.whole,
                        moment.start_s - moment.offset_s,
                      )}
                    </span>
                  ) : null}
                </span>
                <span className={styles.arrow} aria-hidden="true">
                  ↗
                </span>
              </a>
            </li>
          ))}
        </ol>
        {moments.length === 0 ? (
          <p className={styles.quiet}>No moment on this interest yet.</p>
        ) : null}
        {collection.has_more ? (
          <p className={styles.quiet}>
            More moments match this interest; these are the best {moments.length}.
          </p>
        ) : null}
      </article>
    </>
  );
}
